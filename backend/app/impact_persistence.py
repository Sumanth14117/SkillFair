"""Owner-scoped PostgreSQL persistence and aggregate reads for Impact Measurement."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db_models import CandidateExperienceFeedback, CareerWorkflow, ImpactObservation, RecruiterFeedback, WorkflowReview
from .impact_measurement import ImpactMeasurementStore
from .models import (
    ImpactBaselineObservationRequest,
    ImpactCategory,
    CandidateImpactFeedbackRequest,
    ImpactDistributionItem,
    ImpactQuestionDistribution,
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
    "Candidate feedback is voluntary and linked to the signed-in account; respondent identity as a candidate is not independently verified, and submissions are not used for hiring decisions."
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
    db.execute(delete(CandidateExperienceFeedback).where(
        CandidateExperienceFeedback.owner_user_id == owner_id,
        CandidateExperienceFeedback.expires_at.is_not(None),
        CandidateExperienceFeedback.expires_at <= now,
    ).execution_options(synchronize_session=False))
    db.commit()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _record_submission(db: Session, row: RecruiterFeedback | ImpactObservation | CandidateExperienceFeedback, owner_id: UUID, submission_id: UUID, model: type[RecruiterFeedback] | type[ImpactObservation] | type[CandidateExperienceFeedback]) -> bool:
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


def submit_candidate_feedback(
    db: Session,
    owner_id: UUID,
    request: CandidateImpactFeedbackRequest,
    *,
    now: datetime | None = None,
) -> ImpactSubmissionResponse:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _prune_expired_owner_measurements(db, owner_id, current)
    row = CandidateExperienceFeedback(
        owner_user_id=owner_id,
        submission_id=request.submission_id,
        consent_to_aggregate=True,
        application_experience=request.application_experience,
        process_accessibility=request.process_accessibility,
        job_requirements_clarity=request.job_requirements_clarity,
        perceived_fairness=request.perceived_fairness,
        suggestion_categories=list(request.suggestion_categories),
        submitted_at=current,
        expires_at=current + timedelta(days=PERSISTED_MEASUREMENT_RETENTION_DAYS),
    )
    created = _record_submission(db, row, owner_id, request.submission_id, CandidateExperienceFeedback)
    return ImpactSubmissionResponse(
        duplicate=not created,
        message=("This candidate feedback submission was already counted for this account." if not created else
                 "Thank you. Your voluntary feedback is linked to this signed-in account and retained for up to 90 days; free-text comments are not collected."),
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


def _candidate_distributions(rows: list[CandidateExperienceFeedback]) -> tuple[list[ImpactQuestionDistribution], list[ImpactDistributionItem]]:
    from collections import Counter
    from .impact_measurement import MINIMUM_FEEDBACK_SAMPLE

    questions = (
        ("candidate_application_experience", "Application experience", "application_experience"),
        ("candidate_process_accessibility", "Accessibility of the process", "process_accessibility"),
        ("candidate_job_requirements_clarity", "Clarity of job requirements", "job_requirements_clarity"),
        ("candidate_perceived_fairness", "Perceived fairness", "perceived_fairness"),
    )
    sample_size = len(rows)
    availability = "awaiting_data" if sample_size == 0 else "insufficient_sample" if sample_size < MINIMUM_FEEDBACK_SAMPLE else "measured"
    stamps = [_utc(row.submitted_at) for row in rows]
    period_start = min(stamp for stamp in stamps if stamp is not None) if stamps else None
    period_end = max(stamp for stamp in stamps if stamp is not None) if stamps else None
    distributions: list[ImpactQuestionDistribution] = []
    for metric_id, name, field in questions:
        counts = Counter(getattr(row, field) for row in rows)
        distributions.append(ImpactQuestionDistribution(
            metric_id=metric_id,
            name=name,
            sample_size=sample_size,
            data_availability=availability,
            period_start=period_start,
            period_end=period_end,
            distribution=[ImpactDistributionItem(
                label=str(rating),
                value=counts[rating] if sample_size >= MINIMUM_FEEDBACK_SAMPLE else None,
                sample_size=sample_size,
                data_availability=availability,
            ) for rating in range(1, 6)],
        ))
    suggestion_counts = Counter(category for row in rows for category in (row.suggestion_categories or []))
    suggestions = [ImpactDistributionItem(
        label=category,
        value=suggestion_counts[category] if sample_size >= MINIMUM_FEEDBACK_SAMPLE else None,
        sample_size=sample_size,
        data_availability=availability,
    ) for category in ("clarity", "accessibility", "communication", "other")]
    return distributions, suggestions


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
    candidate_rows = db.scalars(select(CandidateExperienceFeedback).where(
        CandidateExperienceFeedback.owner_user_id == owner_id,
        CandidateExperienceFeedback.consent_to_aggregate.is_(True),
        (CandidateExperienceFeedback.expires_at.is_(None)) | (CandidateExperienceFeedback.expires_at > current),
    )).all()
    baseline_rows = db.scalars(select(ImpactObservation).where(
        ImpactObservation.owner_user_id == owner_id,
        ImpactObservation.observation_type == "baseline_duration",
        (ImpactObservation.expires_at.is_(None)) | (ImpactObservation.expires_at > current),
    )).all()

    # Reuse the deterministic aggregation rules over this account's database
    # rows only. This short-lived accumulator is not a persistence source.
    total = len(review_rows) + len(recruiter_rows) + len(candidate_rows) + len(baseline_rows)
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
    for row in candidate_rows:
        aggregate.add_feedback("candidate", row.submission_id, {
            "application_experience": int(row.application_experience),
            "process_accessibility": int(row.process_accessibility),
            "job_requirements_clarity": int(row.job_requirements_clarity),
            "perceived_fairness": int(row.perceived_fairness),
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
    if category in {"all", "candidate_feedback"}:
        candidate_start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc) if start_date else None
        candidate_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) if end_date else None
        selected_candidates = [row for row in candidate_rows if
            (candidate_start is None or _utc(row.submitted_at) >= candidate_start) and
            (candidate_end is None or _utc(row.submitted_at) < candidate_end)]
        candidate_distributions, suggestion_distribution = _candidate_distributions(selected_candidates)
    else:
        candidate_distributions, suggestion_distribution = [], []
    metrics = [
        metric.model_copy(update={
            "limitations": [
                item for item in metric.limitations
                if "unauthenticated" not in item and "process-local" not in item and "30 minutes" not in item
            ] + [_PERSISTENCE_LIMITATION] + ([_CANDIDATE_FEEDBACK_LIMITATION] if metric.category == "candidate_feedback" else []),
        })
        for metric in summary.metrics
    ]
    comparison = summary.baseline_comparison.model_copy(update={
        "limitations": [
            item for item in summary.baseline_comparison.limitations
            if "unauthenticated" not in item and "process-local" not in item
        ] + [_PERSISTENCE_LIMITATION],
    })
    summary = summary.model_copy(update={
        "generated_at": current,
        "pilot_period_label": "Account-scoped pilot · feedback and baseline submissions are reportable for 90 days; workflow history follows workflow retention.",
        "retention_seconds": PERSISTED_MEASUREMENT_RETENTION_SECONDS,
        "dropped_event_count": 0,
        "metrics": metrics,
        "baseline_comparison": comparison,
        "candidate_feedback_distributions": candidate_distributions,
        "candidate_suggestion_distribution": suggestion_distribution,
        "disclaimer": (
            "Measurements are aggregate records associated with the signed-in account; they do not establish "
            "fairness, causation, candidate competence, or improved hiring outcomes. "
            + _PERSISTENCE_LIMITATION + " " + _CANDIDATE_FEEDBACK_LIMITATION
        ),
    })
    return summary
