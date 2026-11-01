"""Owner-scoped PostgreSQL persistence and aggregate reads for Impact Measurement."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db_models import CareerWorkflow, ImpactObservation, RecruiterFeedback, WorkflowReview
from .impact_measurement import ImpactMeasurementStore
from .models import (
    ImpactBaselineObservationRequest,
    ImpactCategory,
    ImpactSummaryResponse,
    ImpactSubmissionResponse,
    LearningPathwaysResponse,
    RecruiterImpactFeedbackRequest,
)


PERSISTED_MEASUREMENT_RETENTION_DAYS = 90
PERSISTED_MEASUREMENT_RETENTION_SECONDS = PERSISTED_MEASUREMENT_RETENTION_DAYS * 24 * 60 * 60
_PERSISTENCE_LIMITATION = (
    "Metrics are scoped to the signed-in account and reflect recorded workflow activity or voluntary submissions."
)
_CANDIDATE_FEEDBACK_LIMITATION = (
    "Persistent candidate feedback is unavailable until a candidate identity or separately reviewed anonymous-submission design exists."
)


class ImpactReferenceNotFound(Exception):
    """A supplied workflow reference is absent, expired, or owned by another account."""


def _prune_expired_owner_measurements(db: Session, owner_id: UUID, now: datetime) -> None:
    """Delete only expired feedback/baselines for this account; never workflows."""
    db.execute(delete(RecruiterFeedback).where(
        RecruiterFeedback.owner_user_id == owner_id,
        RecruiterFeedback.expires_at.is_not(None),
        RecruiterFeedback.expires_at <= now,
    ).execution_options(synchronize_session=False))
    db.execute(delete(ImpactObservation).where(
        ImpactObservation.owner_user_id == owner_id,
        ImpactObservation.observation_type == "baseline_duration",
        ImpactObservation.expires_at.is_not(None),
        ImpactObservation.expires_at <= now,
    ).execution_options(synchronize_session=False))
    db.commit()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _record_submission(db: Session, row: RecruiterFeedback | ImpactObservation, owner_id: UUID, submission_id: UUID, model: type[RecruiterFeedback] | type[ImpactObservation]) -> bool:
    """Insert atomically; uniqueness makes concurrent retries idempotent."""
    existing = db.scalar(select(model.id).where(model.owner_user_id == owner_id, model.submission_id == submission_id))
    if existing is not None:
        return False
    db.add(row)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        # A concurrent identical submission wins the unique constraint. Only
        # treat it as an idempotent retry when the owner's key now exists.
        existing = db.scalar(select(model.id).where(model.owner_user_id == owner_id, model.submission_id == submission_id))
        if existing is not None:
            return False
        raise


def submit_recruiter_feedback(
    db: Session,
    owner_id: UUID,
    request: RecruiterImpactFeedbackRequest,
    *,
    now: datetime | None = None,
) -> ImpactSubmissionResponse:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _prune_expired_owner_measurements(db, owner_id, current)
    workflow_id = None
    if request.workflow_id is not None:
        workflow_id = db.scalar(select(CareerWorkflow.id).where(
            CareerWorkflow.public_id == request.workflow_id,
            CareerWorkflow.owner_user_id == owner_id,
            (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > current),
        ))
        if workflow_id is None:
            raise ImpactReferenceNotFound
    row = RecruiterFeedback(
        owner_user_id=owner_id,
        workflow_id=workflow_id,
        submission_id=request.submission_id,
        consent_to_aggregate=True,
        evidence_usefulness=request.evidence_usefulness,
        explanation_clarity=request.explanation_clarity,
        review_ease=request.review_ease,
        submitted_at=current,
        expires_at=current + timedelta(days=PERSISTED_MEASUREMENT_RETENTION_DAYS),
    )
    created = _record_submission(db, row, owner_id, request.submission_id, RecruiterFeedback)
    return ImpactSubmissionResponse(
        duplicate=not created,
        message=("Recruiter ratings were already counted for this account." if not created else
                 "Voluntary recruiter ratings were recorded with this account for aggregate reporting; no comments are stored."),
    )


def submit_baseline_observation(
    db: Session,
    owner_id: UUID,
    request: ImpactBaselineObservationRequest,
    *,
    now: datetime | None = None,
) -> ImpactSubmissionResponse:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _prune_expired_owner_measurements(db, owner_id, current)
    row = ImpactObservation(
        owner_user_id=owner_id,
        workflow_review_id=None,
        submission_id=request.submission_id,
        observation_type="baseline_duration",
        numeric_value=request.review_duration_seconds,
        created_at=current,
        expires_at=current + timedelta(days=PERSISTED_MEASUREMENT_RETENTION_DAYS),
    )
    created = _record_submission(db, row, owner_id, request.submission_id, ImpactObservation)
    return ImpactSubmissionResponse(
        duplicate=not created,
        message=("This baseline observation was already counted for this account." if not created else
                 "Baseline duration was recorded with this account for aggregate comparison. It is not independently verified."),
    )


def _pathways_confirmed(review: WorkflowReview, workflow_status: str, snapshot: dict[str, object]) -> bool:
    if review.decision != "proceed" or workflow_status != "complete":
        return False
    try:
        if snapshot.get("status") != "complete":
            return False
        result = snapshot.get("result")
        if not isinstance(result, dict):
            return False
        LearningPathwaysResponse.model_validate(result)
        return True
    except Exception:
        return False


def _mark_candidate_feedback_unavailable(summary: ImpactSummaryResponse) -> ImpactSummaryResponse:
    candidate_metrics = [
        metric.model_copy(update={
            "value": None,
            "data_availability": "unavailable",
            "limitations": [item for item in metric.limitations if "process-local" not in item and "unauthenticated" not in item]
            + [_CANDIDATE_FEEDBACK_LIMITATION],
        })
        if metric.category == "candidate_feedback" else metric
        for metric in summary.metrics
    ]
    distributions = [
        item.model_copy(update={"value": None, "data_availability": "unavailable"})
        for item in summary.candidate_rating_distribution
    ]
    return summary.model_copy(update={"metrics": candidate_metrics, "candidate_rating_distribution": distributions})


def get_account_impact_summary(
    db: Session,
    owner_id: UUID,
    *,
    category: ImpactCategory = "all",
    start_date: date | None = None,
    end_date: date | None = None,
    now: datetime | None = None,
) -> ImpactSummaryResponse:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if start_date and end_date and start_date > end_date:
        raise ValueError("start date must not be after end date")
    _prune_expired_owner_measurements(db, owner_id, current)

    review_rows = db.execute(
        select(
            WorkflowReview,
            CareerWorkflow.workflow_status,
            CareerWorkflow.learning_pathways_snapshot,
        )
        .join(CareerWorkflow, and_(
            CareerWorkflow.id == WorkflowReview.workflow_id,
            CareerWorkflow.owner_user_id == WorkflowReview.owner_user_id,
        ))
        .where(
            WorkflowReview.owner_user_id == owner_id,
            CareerWorkflow.owner_user_id == owner_id,
            (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > current),
        )
    ).all()

    recruiter_rows = db.scalars(select(RecruiterFeedback).where(
        RecruiterFeedback.owner_user_id == owner_id,
        RecruiterFeedback.consent_to_aggregate.is_(True),
        (RecruiterFeedback.expires_at.is_(None)) | (RecruiterFeedback.expires_at > current),
    )).all()
    baseline_rows = db.scalars(select(ImpactObservation).where(
        ImpactObservation.owner_user_id == owner_id,
        ImpactObservation.observation_type == "baseline_duration",
        (ImpactObservation.expires_at.is_(None)) | (ImpactObservation.expires_at > current),
    )).all()

    # Reuse the deterministic aggregation rules over this account's database
    # rows only. This short-lived accumulator is not a persistence source.
    total = len(review_rows) + len(recruiter_rows) + len(baseline_rows)
    aggregate = ImpactMeasurementStore(max_records=max(1, total + 1))
    for review, workflow_status, pathways_snapshot in review_rows:
        key = str(review.id)
        started = _utc(review.review_started_at)
        completed = _utc(review.review_completed_at)
        if started is not None:
            aggregate.record_review_start(key, started)
        if review.decision is not None and completed is not None:
            snapshot = pathways_snapshot if isinstance(pathways_snapshot, dict) else {}
            aggregate.record_review_completion(
                key,
                review.decision,
                completed,
                _pathways_confirmed(review, workflow_status, snapshot),
            )
    for row in recruiter_rows:
        aggregate.add_feedback("recruiter", row.submission_id, {
            "evidence_usefulness": row.evidence_usefulness,
            "explanation_clarity": row.explanation_clarity,
            "review_ease": row.review_ease,
        }, _utc(row.submitted_at) or current)
    for row in baseline_rows:
        numeric_value = float(row.numeric_value)
        seconds = int(numeric_value)
        if seconds < 1 or seconds > 14_400 or numeric_value != seconds:
            continue
        request = ImpactBaselineObservationRequest(
            submission_id=row.submission_id,
            consent_to_aggregate=True,
            review_duration_seconds=seconds,
        )
        aggregate.add_baseline(request, _utc(row.created_at) or current)

    summary = aggregate.summary(category=category, start_date=start_date, end_date=end_date, now=current)
    candidate_summary = _mark_candidate_feedback_unavailable(summary)
    metrics = [
        metric.model_copy(update={
            "limitations": [
                item for item in metric.limitations
                if "unauthenticated" not in item and "process-local" not in item and "30 minutes" not in item
            ] + [_PERSISTENCE_LIMITATION],
        })
        for metric in candidate_summary.metrics
    ]
    comparison = candidate_summary.baseline_comparison.model_copy(update={
        "limitations": [
            item for item in candidate_summary.baseline_comparison.limitations
            if "unauthenticated" not in item and "process-local" not in item
        ] + [_PERSISTENCE_LIMITATION],
    })
    summary = candidate_summary.model_copy(update={
        "generated_at": current,
        "pilot_period_label": "Account-scoped pilot · feedback and baseline submissions are reportable for 90 days; workflow history follows workflow retention.",
        "retention_seconds": PERSISTED_MEASUREMENT_RETENTION_SECONDS,
        "dropped_event_count": 0,
        "metrics": metrics,
        "baseline_comparison": comparison,
        "disclaimer": (
            "Measurements are aggregate records associated with the signed-in account; they do not establish "
            "fairness, causation, candidate competence, or improved hiring outcomes. "
            + _PERSISTENCE_LIMITATION + " " + _CANDIDATE_FEEDBACK_LIMITATION
        ),
    })
    return summary
