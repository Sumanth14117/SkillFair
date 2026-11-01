from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import career_orchestrator, main
from app.career_orchestrator import CareerWorkflowStore
from app.models import CareerHumanReviewRequest, CareerOrchestrationRequest, SkillEvidence


def test_access_log_redacts_workflow_run_id():
    run_id = "opaque-test-run-id"
    access_logger = main.logging.getLogger("uvicorn.access")
    assert any(isinstance(item, main._RedactCareerRunIdAccessLog) for item in access_logger.filters)
    record = main.logging.LogRecord(
        "uvicorn.access",
        main.logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "POST", f"/api/career/orchestrator/runs/{run_id}/review", "1.1", 200),
        None,
    )
    record.request_line = f"POST /api/career/orchestrator/runs/{run_id}/review HTTP/1.1"

    assert main._RedactCareerRunIdAccessLog().filter(record)
    rendered = record.getMessage()
    assert run_id not in rendered
    assert run_id not in record.request_line
    assert "/api/career/orchestrator/runs/[redacted]/review" in rendered

    impact_record = main.logging.LogRecord(
        "uvicorn.access", main.logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "POST", f"/api/impact/reviews/{run_id}/start", "1.1", 200), None,
    )
    impact_record.request_line = f"POST /api/impact/reviews/{run_id}/start HTTP/1.1"
    assert main._RedactCareerRunIdAccessLog().filter(impact_record)
    assert run_id not in impact_record.getMessage()
    assert "/api/impact/reviews/[redacted]/start" in impact_record.getMessage()
from app.provenance import AnalysisStore


def evidence(name: str, excerpt: str, kind: str = "explicit") -> SkillEvidence:
    return SkillEvidence(
        skill_name=name,
        evidence_type=kind,
        supporting_excerpt=excerpt,
        relevance_explanation="Located in the analyzed resume context.",
        validation_status="evidence_found" if kind == "explicit" else "inference_unverified",
    )


def request(**overrides) -> CareerOrchestrationRequest:
    values = {
        "analysis_id": "career-analysis-id-123456",
        "job_title": "Data Reporting Analyst",
        "job_description": "A bachelor's degree is required. Create clear reports from operational data.",
        "geography": "Toronto",
        "essential_skills": ["SQL", "Python"],
        "preferred_skills": ["Data reporting"],
    }
    values.update(overrides)
    return CareerOrchestrationRequest(**values)


def configured(monkeypatch, skills=None, store=None, workflow_store=None):
    provenance = store or AnalysisStore()
    analysis_id = provenance.create(skills or [evidence("SQL", "Queried operational data with SQL.")])
    monkeypatch.setattr(main, "analysis_store", provenance)
    monkeypatch.setattr(main, "career_workflow_store", workflow_store if workflow_store is not None else CareerWorkflowStore())
    return analysis_id, provenance


def create(monkeypatch, workflow_store=None, **overrides):
    analysis_id, _ = configured(monkeypatch, workflow_store=workflow_store)
    return main.create_career_workflow(request(analysis_id=analysis_id, **overrides))


def test_successful_run_returns_typed_stages_and_pending_review(monkeypatch):
    response = create(monkeypatch)

    assert response.run_id and response.run_id != response.skill_passport.analysis_id
    assert response.workflow_status == "awaiting_human_review"
    assert [stage.stage for stage in response.stages] == [
        "skills_discovery", "market_intelligence", "job_audit", "inclusive_matching", "human_review", "learning_pathways",
    ]
    assert response.stages[0].status == "complete"
    assert response.stages[1].status == "unavailable"
    assert response.stages[2].status == response.stages[3].status == "complete"
    assert response.stages[4].status == "needs_human_review"
    assert response.stages[5].status == "blocked_pending_review"
    assert response.human_review.status == "pending"
    assert response.learning_pathways.result is None
    assert response.skill_passport.skill_count == 1
    assert response.market_intelligence.data_availability == "unavailable"
    assert response.job_audit.finding_count > 0
    assert response.inclusive_matching.requirements[0].status == "explicitly_evidenced"


def test_get_returns_existing_pending_run_without_mutating_it(monkeypatch):
    response = create(monkeypatch)
    fetched = main.get_career_workflow(response.run_id)
    assert fetched == response
    assert fetched.learning_pathways.status == "blocked_pending_review"
    assert fetched.learning_pathways.result is None
    fetched.workflow_summary = "changed by caller"
    assert main.get_career_workflow(response.run_id).workflow_summary != "changed by caller"


def test_get_returns_existing_reviewed_run(monkeypatch):
    response = create(monkeypatch)
    reviewed = main.review_career_workflow(response.run_id, CareerHumanReviewRequest(
        decision="stop", reviewed_requirement_ids=response.human_review.reviewable_requirement_ids,
    ))
    assert main.get_career_workflow(response.run_id) == reviewed
    assert main.get_career_workflow(response.run_id).human_review.decision == "stop"


def test_get_returns_proceed_run_with_completed_learning_pathways(monkeypatch):
    response = create(monkeypatch)
    reviewed = main.review_career_workflow(response.run_id, CareerHumanReviewRequest(
        decision="proceed", reviewed_requirement_ids=response.human_review.reviewable_requirement_ids,
    ))
    fetched = main.get_career_workflow(response.run_id)
    assert fetched == reviewed
    assert fetched.human_review.status == "recorded"
    assert fetched.human_review.decision == "proceed"
    assert fetched.learning_pathways.status == "complete"
    assert fetched.learning_pathways.result is not None


def test_unknown_and_expired_analysis_ids_are_controlled_404(monkeypatch):
    provenance = AnalysisStore()
    monkeypatch.setattr(main, "analysis_store", provenance)
    monkeypatch.setattr(main, "career_workflow_store", CareerWorkflowStore())
    with pytest.raises(HTTPException) as unknown:
        main.create_career_workflow(request())
    assert unknown.value.status_code == 404

    now = [10.0]
    expiring = AnalysisStore(ttl_seconds=5, clock=lambda: now[0])
    analysis_id = expiring.create([evidence("SQL", "Queried data.")])
    monkeypatch.setattr(main, "analysis_store", expiring)
    now[0] = 16.0
    with pytest.raises(HTTPException) as expired:
        main.create_career_workflow(request(analysis_id=analysis_id))
    assert expired.value.status_code == 404


def test_skill_passport_contains_only_validated_summary_not_resume_text(monkeypatch):
    skills = [
        evidence("SQL", "Queried operational data with SQL."),
        evidence("Python", "Worked with scripts in a project.", "inferred"),
    ]
    analysis_id, _ = configured(monkeypatch, skills)
    response = main.create_career_workflow(request(analysis_id=analysis_id))

    assert response.skill_passport.explicit_count == 1
    assert response.skill_passport.inferred_count == 1
    assert [item.evidence_type for item in response.skill_passport.skills] == ["explicit", "inferred"]
    serialized = response.model_dump_json()
    assert "extracted_text" not in serialized
    assert "supporting_excerpt" not in response.skill_passport.model_dump_json()
    assert "Queried operational data with SQL." in serialized  # Related excerpts remain in Inclusive Matching for recruiter review.


def test_local_analysis_mode_and_detection_method_reach_career_orchestrator(monkeypatch):
    local_skill = SkillEvidence(
        skill_name="Python",
        evidence_type="explicit",
        detection_method="local",
        supporting_excerpt="Technical Skills: Python",
        relevance_explanation="Exact local mention; not proof of proficiency.",
        validation_status="evidence_found",
    )
    store = AnalysisStore()
    analysis_id = store.create([local_skill], analysis_mode="local")
    monkeypatch.setattr(main, "analysis_store", store)
    monkeypatch.setattr(main, "career_workflow_store", CareerWorkflowStore())
    response = main.create_career_workflow(request(
        analysis_id=analysis_id,
        essential_skills=["Python"],
        preferred_skills=[],
    ))

    assert response.skill_passport.analysis_mode == "local"
    assert response.skill_passport.skills[0].detection_method == "local"
    match_evidence = response.inclusive_matching.requirements[0].evidence
    assert match_evidence[0].detection_method == "local"
    assert match_evidence[0].supporting_excerpt == local_skill.supporting_excerpt


def test_market_intelligence_unavailable_does_not_block_workflow(monkeypatch):
    response = create(monkeypatch)
    assert response.market_intelligence.data_availability == "unavailable"
    assert response.market_intelligence.sources == []
    assert response.market_intelligence.insights == []
    assert response.stages[1].status == "unavailable"
    assert response.workflow_status == "awaiting_human_review"


def test_market_provider_failure_is_reported_as_stage_error_and_workflow_continues(monkeypatch):
    class BrokenMarketService:
        def query(self, request):
            raise career_orchestrator.MarketDataProviderError("internal provider detail")

    monkeypatch.setattr(career_orchestrator, "market_intelligence_service", BrokenMarketService())
    response = create(monkeypatch)

    assert response.stages[1].status == "error"
    assert response.market_intelligence.data_availability == "unavailable"
    assert "internal provider detail" not in response.market_intelligence.message
    assert response.stages[2].status == "complete"


def test_job_audit_findings_and_inclusive_matching_are_reused(monkeypatch):
    calls = []
    actual_audit = career_orchestrator.audit_job

    def counted_audit(payload):
        calls.append(payload)
        return actual_audit(payload)

    monkeypatch.setattr(career_orchestrator, "audit_job", counted_audit)
    response = create(monkeypatch)

    assert len(calls) == 1
    assert response.job_audit.finding_count == len(response.inclusive_matching.bias_audit_findings)
    assert response.inclusive_matching.requirements[0].status == "explicitly_evidenced"
    assert response.inclusive_matching.requirements[2].status == "potential_transfer"
    assert response.inclusive_matching.requirements[1].status == "not_evidenced"


def test_audit_is_not_reexecuted_inside_inclusive_matching(monkeypatch):
    count = [0]
    original = career_orchestrator.audit_job

    def counted(payload):
        count[0] += 1
        return original(payload)

    monkeypatch.setattr(career_orchestrator, "audit_job", counted)
    create(monkeypatch)
    assert count[0] == 1


def test_proceed_generates_pathways_only_after_review_of_all_requirements(monkeypatch):
    response = create(monkeypatch)
    assert response.learning_pathways.status == "blocked_pending_review"
    reviewed_ids = response.human_review.reviewable_requirement_ids

    updated = main.review_career_workflow(response.run_id, CareerHumanReviewRequest(
        decision="proceed",
        reviewed_requirement_ids=reviewed_ids,
        review_notes="Reviewed evidence and role criteria.",
    ))

    assert updated.workflow_status == "complete"
    assert updated.human_review.status == "recorded"
    assert updated.human_review.decision == "proceed"
    assert updated.human_review.attested_at.tzinfo is not None
    assert updated.human_review.reviewer_identity_verified is False
    assert updated.learning_pathways.status == "complete"
    assert updated.learning_pathways.result is not None
    assert updated.stages[-1].status == "complete"
    assert main.get_career_workflow(response.run_id) == updated


@pytest.mark.parametrize(("decision", "workflow_status", "learning_status"), [
    ("revise", "revise_required", "blocked_pending_review"),
    ("stop", "stopped", "stopped"),
])
def test_revise_and_stop_do_not_generate_learning_pathways(monkeypatch, decision, workflow_status, learning_status):
    response = create(monkeypatch)
    updated = main.review_career_workflow(response.run_id, CareerHumanReviewRequest(
        decision=decision,
        reviewed_requirement_ids=response.human_review.reviewable_requirement_ids,
    ))

    assert updated.workflow_status == workflow_status
    assert updated.learning_pathways.status == learning_status
    assert updated.learning_pathways.result is None
    assert "silently changed" in updated.workflow_summary or decision == "stop"
    assert main.get_career_workflow(response.run_id) == updated


def test_review_requires_all_known_requirement_ids_and_cannot_be_changed(monkeypatch):
    response = create(monkeypatch)
    with pytest.raises(HTTPException) as incomplete:
        main.review_career_workflow(response.run_id, CareerHumanReviewRequest(
            decision="proceed", reviewed_requirement_ids=response.human_review.reviewable_requirement_ids[:-1],
        ))
    assert incomplete.value.status_code == 409

    submitted = CareerHumanReviewRequest(decision="stop", reviewed_requirement_ids=response.human_review.reviewable_requirement_ids)
    main.review_career_workflow(response.run_id, submitted)
    with pytest.raises(HTTPException) as changed:
        main.review_career_workflow(response.run_id, CareerHumanReviewRequest(
            decision="proceed", reviewed_requirement_ids=response.human_review.reviewable_requirement_ids,
        ))
    assert changed.value.status_code == 409


def test_review_is_idempotent_for_the_same_attestation(monkeypatch):
    response = create(monkeypatch)
    review = CareerHumanReviewRequest(decision="stop", reviewed_requirement_ids=response.human_review.reviewable_requirement_ids)
    first = main.review_career_workflow(response.run_id, review)
    second = main.review_career_workflow(response.run_id, review)
    assert first == second


@pytest.mark.parametrize(("first_decision", "second_decision"), [
    ("proceed", "revise"),
    ("proceed", "stop"),
])
def test_concurrent_conflicting_review_decisions_commit_exactly_one(monkeypatch, first_decision, second_decision):
    pending = create(monkeypatch)
    reviewable_ids = pending.human_review.reviewable_requirement_ids
    barrier = Barrier(2)

    def submit(decision):
        request_value = CareerHumanReviewRequest(decision=decision, reviewed_requirement_ids=reviewable_ids)
        barrier.wait(timeout=5)
        try:
            return main.review_career_workflow(pending.run_id, request_value)
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, decision) for decision in (first_decision, second_decision)]
        results = [future.result(timeout=10) for future in futures]

    successful = [result for result in results if not isinstance(result, HTTPException)]
    rejected = [result for result in results if isinstance(result, HTTPException)]
    assert len(successful) == 1
    assert len(rejected) == 1
    assert rejected[0].status_code == 409

    stored = main.get_career_workflow(pending.run_id)
    assert stored == successful[0]
    assert stored.human_review.status == "recorded"
    if stored.human_review.decision == "proceed":
        assert stored.workflow_status == "complete"
        assert stored.learning_pathways.status == "complete"
        assert stored.learning_pathways.result is not None
        assert next(stage for stage in stored.stages if stage.stage == "learning_pathways").status == "complete"
    else:
        assert stored.human_review.decision in {"revise", "stop"}
        assert stored.learning_pathways.result is None
        assert stored.learning_pathways.status == ("blocked_pending_review" if stored.human_review.decision == "revise" else "stopped")


def test_concurrent_duplicate_proceed_returns_one_consistent_committed_state(monkeypatch):
    pending = create(monkeypatch)
    reviewable_ids = pending.human_review.reviewable_requirement_ids
    barrier = Barrier(2)

    def submit():
        request_value = CareerHumanReviewRequest(decision="proceed", reviewed_requirement_ids=reviewable_ids)
        barrier.wait(timeout=5)
        return main.review_career_workflow(pending.run_id, request_value)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit) for _ in range(2)]
        responses = [future.result(timeout=10) for future in futures]

    assert responses[0] == responses[1]
    stored = main.get_career_workflow(pending.run_id)
    assert stored == responses[0]
    assert stored.human_review.decision == "proceed"
    assert stored.learning_pathways.status == "complete"
    assert stored.learning_pathways.result is not None
    assert next(stage for stage in stored.stages if stage.stage == "learning_pathways").status == "complete"


def test_unknown_and_expired_run_ids_return_controlled_404(monkeypatch):
    monkeypatch.setattr(main, "career_workflow_store", CareerWorkflowStore())
    review = CareerHumanReviewRequest(decision="stop", reviewed_requirement_ids=["req-001"])
    with pytest.raises(HTTPException) as unknown:
        main.review_career_workflow("unknown-run-id", review)
    assert unknown.value.status_code == 404
    with pytest.raises(HTTPException) as unknown_get:
        main.get_career_workflow("unknown-run-id")
    assert unknown_get.value.status_code == 404

    now = [1.0]
    workflows = CareerWorkflowStore(ttl_seconds=2, clock=lambda: now[0])
    # Create directly in the controlled test store with a deterministic clock.
    monkeypatch.setattr(main, "career_workflow_store", workflows)
    response = create(monkeypatch, workflow_store=workflows)
    now[0] = 4.0
    with pytest.raises(HTTPException) as expired:
        main.review_career_workflow(response.run_id, CareerHumanReviewRequest(
            decision="stop", reviewed_requirement_ids=response.human_review.reviewable_requirement_ids,
        ))
    assert expired.value.status_code == 404
    with pytest.raises(HTTPException) as expired_get:
        main.get_career_workflow(response.run_id)
    assert expired_get.value.status_code == 404


def test_run_store_enforces_capacity_expiry_and_deep_copies(monkeypatch):
    now = [0.0]
    store = CareerWorkflowStore(ttl_seconds=4, max_entries=1, clock=lambda: now[0])
    analysis_id, provenance = configured(monkeypatch, workflow_store=store)
    request_one = request(analysis_id=analysis_id)
    skills = provenance.resolve(analysis_id)
    first = career_orchestrator.create_workflow_run(request_one, skills, store)
    first.workflow_summary = "mutated outside store"
    stored = store.resolve(first.run_id)
    assert stored.response.workflow_summary != "mutated outside store"

    second = career_orchestrator.create_workflow_run(request_one, skills, store)
    assert len(store) == 1
    assert store.resolve(first.run_id) is None
    now[0] = 5.0
    assert store.resolve(second.run_id) is None
    assert len(store) == 0


@pytest.mark.parametrize("payload", [
    {"analysis_id": "short", "job_title": "Analyst", "job_description": "Description", "essential_skills": ["SQL"]},
    {"analysis_id": "x" * 24, "job_title": " ", "job_description": "Description", "essential_skills": ["SQL"]},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Description", "essential_skills": []},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Description", "essential_skills": ["x" * 101]},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Description", "essential_skills": ["SQL"] * 31, "preferred_skills": ["Excel"] * 30},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Description", "essential_skills": ["SQL"], "candidate_skills": []},
])
def test_invalid_workflow_requests_are_rejected(payload):
    with pytest.raises(ValidationError):
        CareerOrchestrationRequest.model_validate(payload)


@pytest.mark.parametrize("payload", [
    {"decision": "approve", "reviewed_requirement_ids": ["req-001"]},
    {"decision": "proceed", "reviewed_requirement_ids": []},
    {"decision": "stop", "reviewed_requirement_ids": [""]},
    {"decision": "stop", "reviewed_requirement_ids": ["req-001", "req-001"]},
])
def test_invalid_review_requests_are_rejected(payload):
    with pytest.raises(ValidationError):
        CareerHumanReviewRequest.model_validate(payload)


def test_orchestrator_routes_are_typed_and_existing_routes_remain_present():
    paths = main.app.openapi()["paths"]
    assert "/api/career/orchestrator/runs" in paths
    assert "get" in paths["/api/career/orchestrator/runs/{run_id}"]
    assert "post" in paths["/api/career/orchestrator/runs/{run_id}/review"]
    assert "/api/career/orchestrator/runs/{run_id}/review" in paths
    assert "/api/skills/discover" in paths
    assert "/api/jobs/match" in paths
    assert "/api/jobs/inclusive-match" in paths
    assert "/api/jobs/audit" in paths
    assert "/api/learning/pathways" in paths
    assert "/api/market/intelligence" in paths
