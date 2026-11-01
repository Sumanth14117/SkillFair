import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main
from app.learning_pathways import build_learning_pathways
from app.models import LearningPathwaysRequest, SkillEvidence
from app.provenance import AnalysisStore


def evidence(name: str, excerpt: str, kind: str = "explicit") -> SkillEvidence:
    return SkillEvidence(
        skill_name=name,
        evidence_type=kind,
        supporting_excerpt=excerpt,
        relevance_explanation="Found in the analyzed resume context.",
        validation_status="evidence_found" if kind == "explicit" else "inference_unverified",
    )


def request(**overrides) -> LearningPathwaysRequest:
    values = {
        "job_title": "Data Engineer",
        "required_skills": ["Python", "SQL"],
    }
    values.update(overrides)
    return LearningPathwaysRequest(**values)


def test_request_validates_and_bounds_fields_and_rejects_client_evidence():
    assert request(job_title="  Analyst  ", required_skills=[" SQL "]).job_title == "Analyst"
    with pytest.raises(ValidationError):
        LearningPathwaysRequest(job_title="  ", required_skills=["SQL"])
    with pytest.raises(ValidationError):
        LearningPathwaysRequest(job_title="Engineer", required_skills=[" "])
    with pytest.raises(ValidationError):
        LearningPathwaysRequest(job_title="Engineer", required_skills=["x" * 101])
    with pytest.raises(ValidationError):
        LearningPathwaysRequest(job_title="Engineer", required_skills=["SQL"], analysis_id="too-short")
    with pytest.raises(ValidationError):
        LearningPathwaysRequest(
            job_title="Engineer",
            required_skills=["SQL"],
            candidate_skills=[{"skill_name": "SQL"}],
        )


def test_analysis_distinguishes_explicit_inferred_and_not_evidenced_skills(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([
        evidence("Python", "Built Python services."),
        evidence("Data pipelines", "Automated recurring data workflows.", "inferred"),
    ])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.learning_pathways(request(
        analysis_id=analysis_id,
        required_skills=["python", "Data pipelines", "SQL"],
    ))

    explicit, inferred, missing = result.pathways
    assert result.analysis_linked is True
    assert result.requirement_count == 3
    assert (explicit.required_skill, explicit.evidence_status, explicit.evidence_type) == (
        "python", "explicitly_evidenced", "explicit",
    )
    assert explicit.validation_status == "evidence_found"
    assert (inferred.evidence_status, inferred.evidence_type, inferred.validation_status) == (
        "inferred_unverified", "inferred", "inference_unverified",
    )
    assert (missing.evidence_status, missing.evidenced_skill, missing.evidence_type) == (
        "not_evidenced_in_analysis", None, None,
    )
    assert "does not contain evidence" in missing.activities[0].relevance
    assert "does not mean the person lacks the skill" in result.disclaimer


def test_matching_uses_established_symbol_preserving_normalization(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([
        evidence("C", "Built a utility in C."),
        evidence("C++", "Implemented a service in C++."),
        evidence("C#", "Maintained a C# application.", "inferred"),
    ])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.learning_pathways(request(
        analysis_id=analysis_id,
        required_skills=["C", "C++", "C#"],
    ))

    assert [item.evidence_status for item in result.pathways] == [
        "explicitly_evidenced", "explicitly_evidenced", "inferred_unverified",
    ]
    assert [item.evidenced_skill for item in result.pathways] == ["C", "C++", "C#"]


def test_without_analysis_id_provides_general_pathways_without_candidate_claims():
    result = build_learning_pathways(request())

    assert result.analysis_linked is False
    assert all(item.evidence_status == "not_compared" for item in result.pathways)
    assert all(item.evidenced_skill is None and item.evidence_type is None for item in result.pathways)
    assert all(item.activities for item in result.pathways)
    assert "not courses, certifications" in result.disclaimer


def test_recommendations_are_deterministic_and_do_not_invent_providers_or_completion():
    payload = request(required_skills=["Python", "SQL"])
    first = build_learning_pathways(payload)
    second = build_learning_pathways(payload)

    assert first == second
    serialized = first.model_dump_json().casefold()
    assert "http://" not in serialized and "https://" not in serialized
    assert "certification provider" not in serialized
    assert "has completed training" not in serialized
    assert "completed a course" not in serialized
    assert all(activity.relevance for item in first.pathways for activity in item.activities)


def test_duplicate_requirements_are_deduplicated_by_existing_normalization():
    result = build_learning_pathways(request(required_skills=["SQL", " sql ", "S Q L"]))

    assert [item.required_skill for item in result.pathways] == ["SQL", "S Q L"]
    assert result.requirement_count == 2


def test_unknown_and_expired_analysis_ids_return_controlled_not_found(monkeypatch):
    store = AnalysisStore()
    monkeypatch.setattr(main, "analysis_store", store)
    with pytest.raises(HTTPException) as unknown:
        main.learning_pathways(request(analysis_id="x" * 24))
    assert unknown.value.status_code == 404
    assert "not found or has expired" in unknown.value.detail

    now = [10.0]
    expiring_store = AnalysisStore(ttl_seconds=30, clock=lambda: now[0])
    analysis_id = expiring_store.create([evidence("Python", "Built Python tools.")])
    monkeypatch.setattr(main, "analysis_store", expiring_store)
    now[0] = 41.0
    with pytest.raises(HTTPException) as expired:
        main.learning_pathways(request(analysis_id=analysis_id))
    assert expired.value.status_code == 404
    assert "not found or has expired" in expired.value.detail


def test_route_has_typed_request_and_standard_validation_response():
    operation = main.app.openapi()["paths"]["/api/learning/pathways"]["post"]

    assert "422" in operation["responses"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("LearningPathwaysRequest")
