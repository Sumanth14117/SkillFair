import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main
from app.inclusive_matching import build_inclusive_match
from app.models import InclusiveMatchRequest, SkillEvidence
from app.provenance import AnalysisStore


def evidence(name: str, excerpt: str, kind: str = "explicit") -> SkillEvidence:
    return SkillEvidence(
        skill_name=name,
        evidence_type=kind,
        supporting_excerpt=excerpt,
        relevance_explanation="Identified in the analyzed resume context.",
        validation_status="evidence_found" if kind == "explicit" else "inference_unverified",
    )


def request(**overrides) -> InclusiveMatchRequest:
    values = {
        "analysis_id": "inclusive-analysis-id-123456",
        "job_title": "Data Reporting Analyst",
        "job_description": "Create clear reports from operational data.",
        "essential_skills": ["SQL", "Python", "Data reporting"],
        "preferred_skills": ["Excel"],
    }
    values.update(overrides)
    return InclusiveMatchRequest(**values)


def test_explicit_evidence_is_exact_and_keeps_server_excerpt():
    result = build_inclusive_match(request(), [evidence("SQL", "Wrote SQL queries for weekly reports.")])

    sql, python, reporting, excel = result.requirements
    assert sql.status == "explicitly_evidenced"
    assert sql.importance == "essential"
    assert sql.evidence[0].supporting_excerpt == "Wrote SQL queries for weekly reports."
    assert sql.evidence[0].relationship_type == "exact_name_match"
    assert python.status == excel.status == "not_evidenced"
    assert reporting.status == "potential_transfer"
    assert [item.importance for item in result.requirements] == ["essential", "essential", "essential", "preferred"]
    assert "does not prove the candidate lacks" in python.explanation


def test_inclusive_matching_receives_redacted_provenance_fields(monkeypatch):
    source = evidence("Python", "Built Python tools; sam@example.test")
    source.relevance_explanation = "Contact sam@example.test about Python work."
    store = AnalysisStore()
    analysis_id = store.create([source])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.inclusive_match(request(
        analysis_id=analysis_id,
        essential_skills=["Python"],
        preferred_skills=[],
    ))

    matched = result.requirements[0].evidence[0]
    assert matched.supporting_excerpt == "Built Python tools; [REDACTED EMAIL]"
    assert matched.explanation == "Contact [REDACTED EMAIL] about Python work."
    assert "sam@example.test" not in result.model_dump_json()


def test_inferred_exact_evidence_is_kept_unverified():
    result = build_inclusive_match(request(essential_skills=["Python"], preferred_skills=[]), [
        evidence("Python", "Used scripting in a project.", "inferred"),
    ])

    item = result.requirements[0]
    assert item.status == "inferred_unverified"
    assert item.evidence[0].evidence_type == "inferred"
    assert item.evidence[0].validation_status == "inference_unverified"
    assert result.exact_inferred_count == 1


def test_curated_transfer_is_distinct_from_exact_match_and_preserves_evidence():
    result = build_inclusive_match(request(essential_skills=["Data reporting"], preferred_skills=[]), [
        evidence("SQL", "Prepared SQL extracts for scheduled reporting."),
    ])

    item = result.requirements[0]
    assert item.status == "potential_transfer"
    assert item.evidence[0].skill_name == "SQL"
    assert item.evidence[0].relationship_type == "curated_adjacent_capability"
    assert "not establish" in item.evidence[0].explanation
    assert result.potential_transfer_count == 1


def test_matching_reuses_collision_safe_normalizer_and_deduplicates_case_space_variants():
    result = build_inclusive_match(request(
        essential_skills=["c", "C++", "C#", " SQL "],
        preferred_skills=["sql"],
    ), [
        evidence("C", "Wrote a C program."),
        evidence("C++", "Built a C++ service."),
        evidence("C#", "Maintained a C# application."),
        evidence("sql", "Queried records using SQL."),
    ])

    assert [(item.skill, item.status) for item in result.requirements] == [
        ("c", "explicitly_evidenced"), ("C++", "explicitly_evidenced"),
        ("C#", "explicitly_evidenced"), ("SQL", "explicitly_evidenced"),
    ]
    assert result.requirements[-1].importance == "essential"


def test_bias_audit_findings_are_included_as_advisory_job_criteria(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([evidence("SQL", "Queried business data.")])
    monkeypatch.setattr(main, "analysis_store", store)
    payload = request(
        analysis_id=analysis_id,
        job_description="A bachelor's degree is required. Candidates must be a culture fit.",
        essential_skills=["SQL"],
        preferred_skills=[],
    )

    result = main.inclusive_match(payload)

    assert {finding.category for finding in result.bias_audit_findings} == {
        "credential_requirement", "potentially_exclusionary_wording",
    }
    assert all(finding.human_review_required for finding in result.bias_audit_findings)
    assert result.human_review_required is True
    assert any("necessary" in prompt for prompt in result.human_review_prompts)


def test_analysis_is_resolved_server_side_and_client_evidence_is_forbidden(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([evidence("SQL", "Trusted stored SQL evidence.")])
    monkeypatch.setattr(main, "analysis_store", store)
    payload = request(analysis_id=analysis_id).model_dump()
    payload["candidate_skills"] = [evidence("Python", "Forged client excerpt.").model_dump()]
    with pytest.raises(ValidationError):
        InclusiveMatchRequest.model_validate(payload)

    result = main.inclusive_match(request(analysis_id=analysis_id))
    assert result.requirements[0].evidence[0].supporting_excerpt == "Trusted stored SQL evidence."
    assert "Forged client excerpt" not in result.model_dump_json()


def test_unknown_and_expired_analysis_ids_return_controlled_404(monkeypatch):
    monkeypatch.setattr(main, "analysis_store", AnalysisStore())
    with pytest.raises(HTTPException) as unknown:
        main.inclusive_match(request())
    assert unknown.value.status_code == 404

    now = [100.0]
    store = AnalysisStore(ttl_seconds=30, clock=lambda: now[0])
    analysis_id = store.create([evidence("SQL", "Queried records.")])
    monkeypatch.setattr(main, "analysis_store", store)
    now[0] = 131.0
    with pytest.raises(HTTPException) as expired:
        main.inclusive_match(request(analysis_id=analysis_id))
    assert expired.value.status_code == 404


def test_output_is_deterministic_and_human_review_is_required():
    payload = request()
    skills = [evidence("SQL", "Built a report extract.", "inferred")]
    assert build_inclusive_match(payload, skills) == build_inclusive_match(payload, skills)
    result = build_inclusive_match(payload, skills)
    assert result.human_review_required is True
    assert "does not rank or reject" in result.disclaimer
    assert "demographic" in result.disclaimer


@pytest.mark.parametrize("payload", [
    {"analysis_id": "short", "job_title": "Analyst", "job_description": "Work", "essential_skills": ["SQL"]},
    {"analysis_id": "x" * 24, "job_title": " ", "job_description": "Work"},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": " "},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Work", "essential_skills": [""]},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Work", "unexpected": True},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Work", "essential_skills": ["x" * 101]},
    {"analysis_id": "x" * 24, "job_title": "Analyst", "job_description": "Work", "essential_skills": ["SQL"] * 31, "preferred_skills": ["Excel"] * 30},
])
def test_invalid_requests_are_rejected(payload):
    with pytest.raises(ValidationError):
        InclusiveMatchRequest.model_validate(payload)


def test_route_has_typed_request_and_validation_response():
    operation = main.app.openapi()["paths"]["/api/jobs/inclusive-match"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("InclusiveMatchRequest")
    assert "422" in operation["responses"]
