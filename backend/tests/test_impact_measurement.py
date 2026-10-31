from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.impact_measurement import ImpactMeasurementStore, MAX_RETENTION_SECONDS, MINIMUM_FEEDBACK_SAMPLE
from app import main
from app.career_orchestrator import CareerWorkflowStore
from app.models import (
    CandidateImpactFeedbackRequest, CareerHumanReviewRequest, CareerOrchestrationRequest,
    ImpactBaselineObservationRequest, RecruiterImpactFeedbackRequest, SkillEvidence,
)
from app.provenance import AnalysisStore


BASE = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def metrics_by_id(summary):
    return {metric.metric_id: metric for metric in summary.metrics}


def setup_workflow(monkeypatch, *, workflow_store=None):
    provenance = AnalysisStore()
    analysis_id = provenance.create([SkillEvidence(
        skill_name="SQL", evidence_type="explicit", supporting_excerpt="Used SQL for reporting.",
        relevance_explanation="The resume explicitly mentions SQL.", validation_status="evidence_found",
    )], analysis_mode="local")
    workflow_store = workflow_store if workflow_store is not None else CareerWorkflowStore()
    monkeypatch.setattr(main, "analysis_store", provenance)
    monkeypatch.setattr(main, "career_workflow_store", workflow_store)
    monkeypatch.setattr(main, "impact_measurement_store", ImpactMeasurementStore())
    request = CareerOrchestrationRequest(
        analysis_id=analysis_id, job_title="Data Analyst", job_description="Prepare reports from data.",
        essential_skills=["SQL"],
    )
    return main.create_career_workflow(request)


def test_empty_summary_distinguishes_unavailable_from_zero():
    summary = ImpactMeasurementStore().summary(now=BASE)
    metrics = metrics_by_id(summary)
    avg = metrics["review_duration_average"]
    assert avg.value is None
    assert avg.sample_size == 0
    assert avg.data_availability == "awaiting_data"
    assert "not the same as zero" in " ".join(avg.limitations)
    assert summary.disclaimer.find("unauthenticated") >= 0


def test_review_timing_and_decisions_are_recorded_once_and_pathway_metric_is_confirmed():
    store = ImpactMeasurementStore()
    for index in range(MINIMUM_FEEDBACK_SAMPLE):
        run_id = f"run-locator-{index}-do-not-return"
        started = BASE + timedelta(seconds=index * 10)
        completed = started + timedelta(seconds=60 + index * 10)
        assert store.record_review_start(run_id, started) is True
        assert store.record_review_start(run_id, started + timedelta(seconds=2)) is False
        decision = ("proceed", "revise", "stop", "proceed", "proceed")[index]
        generated = decision == "proceed" and index == 0
        assert store.record_review_completion(run_id, decision, completed, generated) is True
        assert store.record_review_completion(run_id, decision, completed, generated) is False

    summary = store.summary(now=BASE + timedelta(minutes=1))
    metrics = metrics_by_id(summary)
    assert metrics["review_duration_average"].value == pytest.approx(80)
    assert metrics["review_duration_median"].value == 80
    assert metrics["completed_reviews"].value == MINIMUM_FEEDBACK_SAMPLE
    assert metrics["workflow_completion_rate"].value == 100
    assert metrics["learning_pathways_generated"].value == 1
    assert [item.value for item in summary.decision_distribution] == [3, 1, 1]
    assert store.record_count == MINIMUM_FEEDBACK_SAMPLE
    assert all("run-locator" not in str(item.model_dump()) for item in summary.metrics)


def test_incomplete_and_invalid_duration_records_are_not_fabricated():
    store = ImpactMeasurementStore()
    for index in range(MINIMUM_FEEDBACK_SAMPLE):
        store.record_review_start(f"incomplete-{index}-locator", BASE + timedelta(seconds=index))
    incomplete = metrics_by_id(store.summary(now=BASE + timedelta(minutes=1)))["incomplete_reviews"]
    assert incomplete.value == MINIMUM_FEEDBACK_SAMPLE

    invalid_store = ImpactMeasurementStore()
    for index in range(MINIMUM_FEEDBACK_SAMPLE):
        start = BASE + timedelta(seconds=index * 5)
        invalid_store.record_review_start(f"duration-{index}-locator", start)
        end = start if index == 0 else start + timedelta(seconds=10)
        invalid_store.record_review_completion(f"duration-{index}-locator", "stop", end, False)
    duration = metrics_by_id(invalid_store.summary(now=BASE + timedelta(minutes=1)))["review_duration_average"]
    assert duration.value is None
    assert duration.sample_size == MINIMUM_FEEDBACK_SAMPLE - 1
    assert duration.data_availability == "insufficient_sample"
    assert "1 invalid" in " ".join(duration.limitations)


def test_duration_kpis_aggregate_all_records_but_trend_suppresses_each_sparse_time_bucket():
    store = ImpactMeasurementStore()
    for index in range(MINIMUM_FEEDBACK_SAMPLE):
        started = BASE + timedelta(minutes=index * 5)
        run_id = f"split-bucket-{index}"
        store.record_review_start(run_id, started)
        store.record_review_completion(run_id, "proceed", started + timedelta(minutes=1), False)

    summary = store.summary(now=BASE + timedelta(minutes=22))
    metrics = metrics_by_id(summary)
    assert metrics["review_duration_average"].data_availability == "measured"
    assert metrics["review_duration_average"].sample_size == MINIMUM_FEEDBACK_SAMPLE
    assert len(summary.review_duration_trend) == MINIMUM_FEEDBACK_SAMPLE
    assert all(point.data_availability == "insufficient_sample" for point in summary.review_duration_trend)
    assert all(point.sample_size == 1 and point.average_seconds is None for point in summary.review_duration_trend)


def test_feedback_validation_duplicate_protection_and_separate_aggregates():
    store = ImpactMeasurementStore()
    feedback = RecruiterImpactFeedbackRequest(
        submission_id=uuid4(), consent_to_aggregate=True, evidence_usefulness=4, explanation_clarity=3, review_ease=5,
    )
    for index in range(MINIMUM_FEEDBACK_SAMPLE - 1):
        payload = feedback.model_copy(update={"submission_id": uuid4()})
        assert store.add_feedback("recruiter", payload.submission_id, {
            "evidence_usefulness": payload.evidence_usefulness,
            "explanation_clarity": payload.explanation_clarity,
            "review_ease": payload.review_ease,
        }, BASE + timedelta(seconds=index)) is True
    assert store.add_feedback("recruiter", feedback.submission_id, {
        "evidence_usefulness": feedback.evidence_usefulness,
        "explanation_clarity": feedback.explanation_clarity,
        "review_ease": feedback.review_ease,
    }, BASE) is True
    assert store.add_feedback("recruiter", feedback.submission_id, {
        "evidence_usefulness": 1, "explanation_clarity": 1, "review_ease": 1,
    }, BASE) is False
    summary = store.summary(now=BASE + timedelta(minutes=1))
    metrics = metrics_by_id(summary)
    assert metrics["recruiter_evidence_usefulness"].value == 4
    assert metrics["recruiter_evidence_usefulness"].sample_size == MINIMUM_FEEDBACK_SAMPLE
    assert not any(metric.category == "candidate_feedback" and metric.value is not None for metric in summary.metrics)
    assert all(item.data_availability == "measured" for item in summary.recruiter_rating_distribution)


def test_candidate_feedback_requires_explicit_consent_and_comment_is_not_a_supported_field():
    base = {
        "submission_id": str(uuid4()), "consent_to_aggregate": True,
        "skill_representation_clarity": 5, "understanding_of_skills": 4,
        "perceived_transparency": 4, "overall_experience": 5,
    }
    request = CandidateImpactFeedbackRequest.model_validate(base)
    assert request.overall_experience == 5
    with pytest.raises(ValidationError):
        CandidateImpactFeedbackRequest.model_validate({**base, "consent_to_aggregate": False})
    with pytest.raises(ValidationError):
        CandidateImpactFeedbackRequest.model_validate({**base, "overall_experience": 6})
    with pytest.raises(ValidationError):
        CandidateImpactFeedbackRequest.model_validate({**base, "comment": "private personal details"})


def test_category_and_date_filters_limit_aggregates():
    store = ImpactMeasurementStore()
    for index in range(MINIMUM_FEEDBACK_SAMPLE):
        payload = RecruiterImpactFeedbackRequest(submission_id=uuid4(), consent_to_aggregate=True, evidence_usefulness=5, explanation_clarity=4, review_ease=3)
        store.add_feedback("recruiter", payload.submission_id, {
            "evidence_usefulness": payload.evidence_usefulness,
            "explanation_clarity": payload.explanation_clarity,
            "review_ease": payload.review_ease,
        }, BASE + timedelta(days=index))
    selected = store.summary(category="recruiter_feedback", start_date=BASE.date(), end_date=(BASE + timedelta(days=2)).date(), now=BASE + timedelta(days=3))
    assert selected.category == "recruiter_feedback"
    assert len(selected.metrics) == 4
    assert all(metric.sample_size == 3 and metric.value is None for metric in selected.metrics)
    assert all(metric.data_availability == "insufficient_sample" for metric in selected.metrics)
    with pytest.raises(ValueError):
        store.summary(start_date=BASE.date(), end_date=(BASE - timedelta(days=1)).date())


def test_baseline_comparison_requires_samples_and_reports_noncausal_difference():
    store = ImpactMeasurementStore()
    for index in range(MINIMUM_FEEDBACK_SAMPLE):
        store.add_baseline(ImpactBaselineObservationRequest(submission_id=uuid4(), consent_to_aggregate=True, review_duration_seconds=100), BASE + timedelta(seconds=index))
        run_id = f"pilot-{index}-locator"
        start = BASE + timedelta(seconds=100 + index * 10)
        store.record_review_start(run_id, start)
        store.record_review_completion(run_id, "proceed", start + timedelta(seconds=80), True)
    comparison = store.summary(category="baseline", now=BASE + timedelta(minutes=1)).baseline_comparison
    assert comparison.data_availability == "measured"
    assert comparison.baseline_sample_size == comparison.pilot_sample_size == MINIMUM_FEEDBACK_SAMPLE
    assert comparison.absolute_difference_seconds == -20
    assert comparison.percentage_difference == -20
    assert any("does not establish causation" in item for item in comparison.limitations)


def test_storage_is_bounded_and_records_expire_within_thirty_minutes():
    now = [10.0]
    store = ImpactMeasurementStore(ttl_seconds=MAX_RETENTION_SECONDS, max_records=1, clock=lambda: now[0])
    store.record_review_start("only-locator", BASE)
    assert store.record_review_start("capacity-locator", BASE) is None
    now[0] += MAX_RETENTION_SECONDS
    assert store.record_count == 0
    assert store.record_review_start("after-expiry-locator", BASE) is True

    with pytest.raises(ValueError):
        ImpactMeasurementStore(ttl_seconds=MAX_RETENTION_SECONDS + 1)


@pytest.mark.parametrize(("decision", "pathways_generated"), [("proceed", True), ("revise", False), ("stop", False)])
def test_review_start_and_completion_routes_measure_committed_backend_state(monkeypatch, decision, pathways_generated):
    workflow = setup_workflow(monkeypatch)
    first = main.start_impact_review(workflow.run_id)
    duplicate = main.start_impact_review(workflow.run_id)
    assert first.accepted and not first.duplicate
    assert duplicate.accepted and duplicate.duplicate

    review = CareerHumanReviewRequest(decision=decision, reviewed_requirement_ids=workflow.human_review.reviewable_requirement_ids)
    completed = main.review_career_workflow(workflow.run_id, review)
    assert completed.human_review.decision == decision
    assert completed.learning_pathways.status == ("complete" if decision == "proceed" else "blocked_pending_review" if decision == "revise" else "stopped")
    repeated = main.review_career_workflow(workflow.run_id, review)
    assert repeated == completed

    # Legacy pure-domain helpers retain only a transient start marker in this
    # unit test; dashboard decisions now come from persisted workflow rows.
    reviews, feedback, baseline = main.impact_measurement_store.snapshot()
    assert len(reviews) == 1 and not feedback and not baseline
    assert reviews[0].decision is None
    assert reviews[0].started_at is not None and reviews[0].completed_at is None
    with pytest.raises(HTTPException) as completed_start:
        main.start_impact_review(workflow.run_id)
    assert completed_start.value.status_code == 409


def test_review_start_unknown_or_expired_workflow_is_rejected_without_recording(monkeypatch):
    monkeypatch.setattr(main, "career_workflow_store", CareerWorkflowStore())
    monkeypatch.setattr(main, "impact_measurement_store", ImpactMeasurementStore())
    with pytest.raises(HTTPException) as unknown:
        main.start_impact_review("unknown-workflow-locator")
    assert unknown.value.status_code == 404
    assert main.impact_measurement_store.record_count == 0

    now = [1.0]
    expired_store = CareerWorkflowStore(ttl_seconds=2, clock=lambda: now[0])
    workflow = setup_workflow(monkeypatch, workflow_store=expired_store)
    now[0] = 4.0
    with pytest.raises(HTTPException) as expired:
        main.start_impact_review(workflow.run_id)
    assert expired.value.status_code == 404
    assert main.impact_measurement_store.record_count == 0


def test_feedback_routes_return_only_submission_receipts_and_aggregate_values(monkeypatch):
    store = ImpactMeasurementStore()
    request = RecruiterImpactFeedbackRequest(
        submission_id=uuid4(), consent_to_aggregate=True, evidence_usefulness=4,
        explanation_clarity=4, review_ease=5,
    )
    ratings = request.model_dump(include={"evidence_usefulness", "explanation_clarity", "review_ease"})
    assert store.add_feedback("recruiter", request.submission_id, ratings, BASE) is True
    assert store.add_feedback("recruiter", request.submission_id, ratings, BASE) is False

    candidate = CandidateImpactFeedbackRequest(
        submission_id=uuid4(), consent_to_aggregate=True, skill_representation_clarity=5,
        understanding_of_skills=4, perceived_transparency=4, overall_experience=5,
    )
    candidate_ratings = candidate.model_dump(include={"skill_representation_clarity", "understanding_of_skills", "perceived_transparency", "overall_experience"})
    assert store.add_feedback("candidate", candidate.submission_id, candidate_ratings, BASE) is True
    summary = store.summary(now=BASE + timedelta(seconds=1))
    assert {metric.category for metric in summary.metrics} >= {"recruiter_feedback", "candidate_feedback"}
    assert len(store.snapshot()[1]) == 2


def test_baseline_route_is_idempotent_and_api_exposes_aggregate_only_contract(monkeypatch):
    from app import main as skillfair_main

    store = ImpactMeasurementStore()
    request = ImpactBaselineObservationRequest(submission_id=uuid4(), consent_to_aggregate=True, review_duration_seconds=120)
    assert store.add_baseline(request, BASE) is True
    assert store.add_baseline(request, BASE) is False
    paths = skillfair_main.app.openapi()["paths"]
    assert "/api/impact/summary" in paths and "get" in paths["/api/impact/summary"]
    assert "/api/impact/reviews/{run_id}/start" in paths
    assert "/api/impact/feedback/recruiter" in paths
    assert "/api/impact/feedback/candidate" in paths
    assert "/api/impact/baseline-observations" in paths
    assert not any("individual" in path or "records" in path for path in paths if path.startswith("/api/impact/"))
