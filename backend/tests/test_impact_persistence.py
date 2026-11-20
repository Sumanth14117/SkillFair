from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database, main
from app.auth import get_auth_context
from app.db_models import Base, CareerWorkflow, ImpactObservation, RecruiterFeedback, ResumeAnalysis, User, WorkflowReview
from app.impact_persistence import (
    ImpactReferenceNotFound,
    PERSISTED_MEASUREMENT_RETENTION_DAYS,
    get_account_impact_summary,
    submit_baseline_observation,
    submit_recruiter_feedback,
)
from app.models import ImpactBaselineObservationRequest, RecruiterImpactFeedbackRequest


@pytest.fixture
def impact_db():
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _user(db: Session, email: str) -> User:
    row = User(email_normalized=email, password_hash="$argon2id$test")
    db.add(row)
    db.flush()
    return row


def _workflow(db: Session, owner_id, public_id: str) -> CareerWorkflow:
    analysis = ResumeAnalysis(public_id=f"analysis-{public_id}", owner_user_id=owner_id, analysis_mode="local")
    db.add(analysis)
    db.flush()
    workflow = CareerWorkflow(
        public_id=public_id,
        owner_user_id=owner_id,
        analysis_id=analysis.id,
        job_title="Analyst",
        job_description="Analyze data.",
        geography=None,
        essential_skills=["SQL"],
        preferred_skills=[],
        workflow_status="awaiting_human_review",
        reviewable_requirement_ids=["essential:sql"],
        stages_snapshot=[],
        skill_passport_snapshot={},
        market_intelligence_snapshot={},
        job_audit_snapshot={},
        inclusive_matching_snapshot={},
        learning_pathways_snapshot={"status": "blocked_pending_review", "result": None},
        workflow_summary="Pending review.",
    )
    db.add(workflow)
    db.flush()
    return workflow


def test_recruiter_feedback_and_baseline_are_persisted_and_owner_scoped(impact_db):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with Session(impact_db) as db:
        owner_a, owner_b = _user(db, "impact-a@example.test"), _user(db, "impact-b@example.test")
        workflow_a = _workflow(db, owner_a.id, "workflow-locator-impact-a")
        workflow_b = _workflow(db, owner_b.id, "workflow-locator-impact-b")
        db.commit()

        feedback = RecruiterImpactFeedbackRequest(
            submission_id=uuid4(), workflow_id=workflow_a.public_id, consent_to_aggregate=True,
            evidence_usefulness=5, explanation_clarity=4, review_ease=3,
        )
        first = submit_recruiter_feedback(db, owner_a.id, feedback, now=now)
        again = submit_recruiter_feedback(db, owner_a.id, feedback, now=now + timedelta(seconds=1))
        assert first.duplicate is False and again.duplicate is True

        baseline = ImpactBaselineObservationRequest(
            submission_id=uuid4(), consent_to_aggregate=True, review_duration_seconds=120,
        )
        assert submit_baseline_observation(db, owner_a.id, baseline, now=now).duplicate is False
        assert db.scalar(select(RecruiterFeedback).where(RecruiterFeedback.owner_user_id == owner_a.id)) is not None
        observation = db.scalar(select(ImpactObservation).where(ImpactObservation.owner_user_id == owner_a.id))
        assert observation is not None and observation.observation_type == "baseline_duration"
        assert observation.expires_at.replace(tzinfo=timezone.utc) == now + timedelta(days=PERSISTED_MEASUREMENT_RETENTION_DAYS)

        with pytest.raises(ImpactReferenceNotFound):
            submit_recruiter_feedback(db, owner_a.id, feedback.model_copy(update={
                "submission_id": uuid4(), "workflow_id": workflow_b.public_id,
            }), now=now)

        summary_a = get_account_impact_summary(db, owner_a.id, now=now + timedelta(seconds=2))
        summary_b = get_account_impact_summary(db, owner_b.id, now=now + timedelta(seconds=2))
        assert next(m for m in summary_a.metrics if m.metric_id == "recruiter_evidence_usefulness").sample_size == 1
        assert next(m for m in summary_b.metrics if m.metric_id == "recruiter_evidence_usefulness").sample_size == 0
        assert next(metric for metric in summary_a.metrics if metric.metric_id == "candidate_application_experience").data_availability == "awaiting_data"
        assert summary_a.retention_seconds == 90 * 24 * 60 * 60
        payload = summary_a.model_dump(mode="json")
        serialized = str(payload)
        assert str(owner_a.id) not in serialized and str(feedback.submission_id) not in serialized
        assert workflow_a.public_id not in serialized


def test_persistent_summary_preserves_empty_and_measured_zero_semantics(impact_db):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with Session(impact_db) as db:
        owner = _user(db, "impact-empty@example.test")
        db.commit()
        summary = get_account_impact_summary(db, owner.id, now=now)
        durations = next(metric for metric in summary.metrics if metric.metric_id == "review_duration_average")
        assert durations.value is None and durations.sample_size == 0
        assert durations.data_availability == "awaiting_data"
        assert next(metric for metric in summary.metrics if metric.metric_id == "candidate_application_experience").data_availability == "awaiting_data"
        assert "fairness" in summary.disclaimer


def test_persistent_summary_excludes_recruiter_feedback_without_aggregate_consent(impact_db):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with Session(impact_db) as db:
        owner = _user(db, "impact-no-consent@example.test")
        db.add(RecruiterFeedback(
            owner_user_id=owner.id,
            workflow_id=None,
            submission_id=uuid4(),
            consent_to_aggregate=False,
            evidence_usefulness=5,
            explanation_clarity=5,
            review_ease=5,
            submitted_at=now,
            expires_at=now + timedelta(days=30),
        ))
        db.commit()

        summary = get_account_impact_summary(db, owner.id, now=now)
        feedback_metrics = [item for item in summary.metrics if item.category == "recruiter_feedback"]
        assert feedback_metrics
        assert all(item.sample_size == 0 and item.value is None for item in feedback_metrics)
        assert all(item.data_availability == "awaiting_data" for item in feedback_metrics)


def test_persistent_metrics_count_only_completed_decisions_and_apply_24_hour_duration_bound(impact_db):
    from app.models import LearningPathwaysResponse

    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    valid_pathways = LearningPathwaysResponse(
        job_title="Analyst", analysis_linked=False, requirement_count=0, pathways=[], disclaimer="Advisory only."
    ).model_dump(mode="json")
    with Session(impact_db) as db:
        owner = _user(db, "impact-duration-bound@example.test")
        completed_workflow = _workflow(db, owner.id, "workflow-exact-24h")
        completed_workflow.workflow_status = "complete"
        completed_workflow.learning_pathways_snapshot = {"status": "complete", "result": valid_pathways}
        db.add(WorkflowReview(
            workflow_id=completed_workflow.id,
            owner_user_id=owner.id,
            decision="proceed",
            reviewed_requirement_ids=["essential:sql"],
            review_started_at=now - timedelta(hours=24),
            review_completed_at=now,
        ))
        over_limit_workflow = _workflow(db, owner.id, "workflow-over-24h")
        db.add(WorkflowReview(
            workflow_id=over_limit_workflow.id,
            owner_user_id=owner.id,
            decision="stop",
            reviewed_requirement_ids=["essential:sql"],
            review_started_at=now - timedelta(hours=24, seconds=1),
            review_completed_at=now,
        ))
        for index in range(4):
            incomplete_workflow = _workflow(db, owner.id, f"workflow-incomplete-{index}")
            db.add(WorkflowReview(
                workflow_id=incomplete_workflow.id,
                owner_user_id=owner.id,
                decision=None,
                reviewed_requirement_ids=[],
                review_started_at=now - timedelta(minutes=2),
                review_completed_at=None,
            ))
        db.commit()

        summary = get_account_impact_summary(db, owner.id, now=now)
        metrics = {item.metric_id: item for item in summary.metrics}
        assert metrics["completed_reviews"].value == 2
        assert metrics["completed_reviews"].sample_size == 6
        assert metrics["incomplete_reviews"].value == 4
        assert metrics["incomplete_reviews"].sample_size == 6
        assert metrics["learning_pathways_generated"].value == 1
        assert metrics["learning_pathways_generated"].sample_size == 6
        assert metrics["review_duration_average"].sample_size == 1
        assert metrics["review_duration_average"].value is None
        assert metrics["review_duration_average"].data_availability == "insufficient_sample"
        decisions = {item.label: item for item in summary.decision_distribution}
        assert {key: item.value for key, item in decisions.items()} == {"proceed": 1, "revise": 0, "stop": 1}


def test_learning_pathways_metric_requires_proceed_and_valid_completed_result(impact_db):
    from app.models import LearningPathwaysResponse

    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    valid_result = LearningPathwaysResponse(
        job_title="Analyst", analysis_linked=False, requirement_count=0, pathways=[], disclaimer="Advisory only."
    ).model_dump(mode="json")
    with Session(impact_db) as db:
        owner = _user(db, "impact-pathway-integrity@example.test")
        cases = [
            ("proceed-valid", "proceed", "complete", {"status": "complete", "result": valid_result}, True),
            ("proceed-blocked", "proceed", "complete", {"status": "blocked_pending_review", "result": None}, False),
            ("revise-valid", "revise", "revise_required", {"status": "complete", "result": valid_result}, False),
            ("stop-valid", "stop", "stopped", {"status": "complete", "result": valid_result}, False),
        ]
        expected_by_id = {}
        for name, decision, status, snapshot, expected in cases:
            workflow = _workflow(db, owner.id, f"workflow-{name}")
            expected_by_id[workflow.public_id] = expected
            workflow.workflow_status = status
            workflow.learning_pathways_snapshot = snapshot
            db.add(WorkflowReview(
                workflow_id=workflow.id,
                owner_user_id=owner.id,
                decision=decision,
                reviewed_requirement_ids=["essential:sql"],
                review_started_at=now - timedelta(minutes=1),
                review_completed_at=now,
            ))
        db.commit()

        summary = get_account_impact_summary(db, owner.id, now=now)
        pathways_metric = next(item for item in summary.metrics if item.metric_id == "learning_pathways_generated")
        assert pathways_metric.sample_size == 4
        assert pathways_metric.value is None  # the minimum sample threshold remains five
        assert pathways_metric.data_availability == "insufficient_sample"
        from app.impact_persistence import _pathways_confirmed
        for public_id, expected in expected_by_id.items():
            workflow = db.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == public_id))
            assert workflow is not None
            review = db.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == workflow.id))
            assert review is not None
            assert _pathways_confirmed(review, workflow.workflow_status, workflow.learning_pathways_snapshot) is expected


def test_persistent_review_duration_is_not_limited_by_old_memory_ttl_and_expired_feedback_is_excluded(impact_db):
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with Session(impact_db) as db:
        owner = _user(db, "impact-retention@example.test")
        workflow = _workflow(db, owner.id, "workflow-long-review-impact")
        review_start = now - timedelta(hours=1)
        db.add(WorkflowReview(
            workflow_id=workflow.id,
            owner_user_id=owner.id,
            decision="stop",
            reviewed_requirement_ids=["essential:sql"],
            review_started_at=review_start,
            review_completed_at=now,
        ))
        db.add(RecruiterFeedback(
            owner_user_id=owner.id,
            workflow_id=workflow.id,
            submission_id=uuid4(),
            consent_to_aggregate=True,
            evidence_usefulness=5,
            explanation_clarity=5,
            review_ease=5,
            submitted_at=now - timedelta(days=91),
            expires_at=now - timedelta(days=1),
        ))
        db.commit()
        summary = get_account_impact_summary(db, owner.id, now=now)
        duration = next(item for item in summary.metrics if item.metric_id == "review_duration_average")
        recruiter = next(item for item in summary.metrics if item.metric_id == "recruiter_evidence_usefulness")
        assert duration.sample_size == 1
        assert duration.data_availability == "insufficient_sample"
        assert recruiter.sample_size == 0
        assert recruiter.data_availability == "awaiting_data"
        assert db.scalar(select(RecruiterFeedback.id).where(RecruiterFeedback.owner_user_id == owner.id)) is None


def test_feedback_transaction_failure_rolls_back_new_submission(impact_db):
    from sqlalchemy import event

    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with Session(impact_db) as db:
        owner = _user(db, "impact-rollback@example.test")
        db.commit()
        payload = RecruiterImpactFeedbackRequest(
            submission_id=uuid4(), consent_to_aggregate=True,
            evidence_usefulness=3, explanation_clarity=3, review_ease=3,
        )

        def fail_before_commit(_session):
            raise RuntimeError("injected persistence failure")

        event.listen(db, "before_commit", fail_before_commit)
        try:
            with pytest.raises(RuntimeError, match="injected persistence failure"):
                submit_recruiter_feedback(db, owner.id, payload, now=now)
        finally:
            event.remove(db, "before_commit", fail_before_commit)
            db.rollback()
        assert db.scalar(select(RecruiterFeedback.id).where(RecruiterFeedback.owner_user_id == owner.id)) is None


def test_persistent_summary_preserves_date_and_category_filters(impact_db):
    first_day = datetime(2026, 10, 20, tzinfo=timezone.utc)
    with Session(impact_db) as db:
        owner = _user(db, "impact-filter@example.test")
        for day in range(5):
            payload = RecruiterImpactFeedbackRequest(
                submission_id=uuid4(), consent_to_aggregate=True,
                evidence_usefulness=5, explanation_clarity=4, review_ease=3,
            )
            submit_recruiter_feedback(db, owner.id, payload, now=first_day + timedelta(days=day))
        selected = get_account_impact_summary(
            db,
            owner.id,
            category="recruiter_feedback",
            start_date=first_day.date(),
            end_date=(first_day + timedelta(days=2)).date(),
            now=first_day + timedelta(days=6),
        )
        assert selected.category == "recruiter_feedback"
        assert len(selected.metrics) == 4
        assert all(item.sample_size == 3 for item in selected.metrics)
        assert all(item.data_availability == "insufficient_sample" for item in selected.metrics)
        assert selected.baseline_comparison.data_availability == "unavailable"


def test_impact_routes_require_authentication_and_candidate_feedback_is_available():
    expected_paths = {
        "/api/impact/summary", "/api/impact/feedback/recruiter",
        "/api/impact/feedback/candidate", "/api/impact/baseline-observations",
        "/api/impact/reviews/{run_id}/start",
    }
    routes = {route.path: route for route in main.app.routes if isinstance(route, APIRoute)}
    assert expected_paths <= routes.keys()

    def dependency_calls(dependant):
        result = {dependant.call}
        for child in dependant.dependencies:
            result.update(dependency_calls(child))
        return result

    for path in expected_paths:
        assert get_auth_context in dependency_calls(routes[path].dependant)
    assert "POST" in routes["/api/impact/feedback/candidate"].methods
