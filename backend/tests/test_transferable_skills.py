import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main
from app.models import SkillEvidence, TransferableSkillsRequest
from app.provenance import AnalysisStore
from app.transferable_skills import build_transferable_skills


def evidence(name: str, excerpt: str, kind: str = "explicit") -> SkillEvidence:
    return SkillEvidence(
        skill_name=name,
        evidence_type=kind,
        supporting_excerpt=excerpt,
        relevance_explanation="Found in analyzed resume context.",
        validation_status="evidence_found" if kind == "explicit" else "inference_unverified",
    )


def request(**overrides) -> TransferableSkillsRequest:
    values = {
        "source_role": "Operations Analyst",
        "target_role": "Data Reporting Analyst",
        "target_requirements": ["SQL", "Data reporting"],
    }
    values.update(overrides)
    return TransferableSkillsRequest(**values)


def test_explicit_exact_skill_match_preserves_stored_excerpt_and_evidence_status(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([evidence("SQL", "Queried customer operations data using SQL.")])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.transferable_skills(request(analysis_id=analysis_id, target_requirements=["sql"]))
    match = result.results[0]

    assert result.candidate_comparison_status == "compared"
    assert match.status == "exact_match"
    assert match.candidate_evidence[0].relationship_type == "exact_name_match"
    assert match.candidate_evidence[0].evidence_type == "explicit"
    assert match.candidate_evidence[0].validation_status == "evidence_found"
    assert match.candidate_evidence[0].supporting_excerpt == "Queried customer operations data using SQL."
    assert "does not establish proficiency" in match.explanation


def test_inferred_evidence_remains_unverified_for_exact_and_adjacent_matches(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([evidence("SQL", "Worked with data tables in a project.", "inferred")])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.transferable_skills(request(analysis_id=analysis_id))

    exact, related = result.results
    assert exact.status == "exact_match"
    assert exact.candidate_evidence[0].evidence_type == "inferred"
    assert exact.candidate_evidence[0].validation_status == "inference_unverified"
    assert related.status == "potential_transfer"
    assert related.candidate_evidence[0].evidence_type == "inferred"
    assert related.candidate_evidence[0].validation_status == "inference_unverified"
    assert "not an equivalent skill" in related.candidate_evidence[0].explanation


def test_curated_adjacent_capability_is_distinct_from_exact_match(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([evidence("SQL", "Built SQL queries for scheduled reports.")])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.transferable_skills(request(
        analysis_id=analysis_id,
        target_requirements=["SQL", "Data reporting", "Python"],
    ))

    exact, related, unrelated = result.results
    assert exact.status == "exact_match"
    assert related.status == "potential_transfer"
    assert related.candidate_evidence[0].relationship_type == "curated_adjacent_capability"
    assert "reporting ownership" in related.candidate_evidence[0].explanation
    assert unrelated.status == "not_evidenced"
    assert unrelated.candidate_evidence == []


def test_missing_evidence_is_not_described_as_lacking_a_skill(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([evidence("Customer Support", "Resolved customer issues.")])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.transferable_skills(request(analysis_id=analysis_id, target_requirements=["Python"]))

    missing = result.results[0]
    assert missing.status == "not_evidenced"
    assert missing.candidate_evidence == []
    assert "does not prove the person lacks the skill" in missing.explanation


def test_without_analysis_id_returns_generic_role_exploration_not_candidate_claims():
    result = build_transferable_skills(request())

    assert result.analysis_linked is False
    assert result.candidate_comparison_status == "not_compared"
    assert all(item.status == "not_compared" for item in result.results)
    assert all(item.candidate_evidence == [] for item in result.results)
    assert all("generic role-to-role" in item.explanation for item in result.results)
    assert result.exploration_prompts == []


def test_no_analysis_and_no_requirements_still_provides_general_exploration_prompts():
    result = build_transferable_skills(request(target_requirements=[]))

    assert result.analysis_linked is False
    assert result.candidate_comparison_status == "not_compared"
    assert result.results == []
    assert len(result.exploration_prompts) == 3
    assert all("Operations Analyst" in prompt and "Data Reporting Analyst" in prompt for prompt in result.exploration_prompts)


def test_empty_analysis_and_no_requirements_are_not_mislabeled_as_comparison():
    result = build_transferable_skills(request(target_requirements=[]), candidate_skills=[])

    assert result.analysis_linked is True
    assert result.candidate_comparison_status == "not_compared"
    assert result.exploration_prompts


def test_analysis_and_excerpts_come_only_from_server_side_provenance(monkeypatch):
    stored_excerpt = "Created spreadsheet reports for weekly operations."
    store = AnalysisStore()
    analysis_id = store.create([evidence("Excel", stored_excerpt)])
    monkeypatch.setattr(main, "analysis_store", store)
    client_payload = {
        "analysis_id": analysis_id,
        "source_role": "Analyst",
        "target_role": "Reporting Analyst",
        "target_requirements": ["Data reporting"],
        "candidate_skills": [{
            "skill_name": "SQL",
            "evidence_type": "explicit",
            "supporting_excerpt": "Forged excerpt",
            "relevance_explanation": "Client supplied",
            "validation_status": "evidence_found",
        }],
    }

    with pytest.raises(ValidationError):
        TransferableSkillsRequest.model_validate(client_payload)

    clean_payload = {key: value for key, value in client_payload.items() if key != "candidate_skills"}
    result = main.transferable_skills(TransferableSkillsRequest.model_validate(clean_payload))
    assert result.results[0].candidate_evidence[0].skill_name == "Excel"
    assert result.results[0].candidate_evidence[0].supporting_excerpt == stored_excerpt
    assert "Forged excerpt" not in result.model_dump_json()


def test_exact_normalization_does_not_blur_c_cpp_or_csharp(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([
        evidence("C", "Wrote a C utility."),
        evidence("C++", "Built a C++ service."),
        evidence("C#", "Maintained a C# application."),
    ])
    monkeypatch.setattr(main, "analysis_store", store)

    result = main.transferable_skills(request(
        analysis_id=analysis_id,
        target_requirements=["C", "C++", "C#"],
    ))

    assert [item.status for item in result.results] == ["exact_match", "exact_match", "exact_match"]
    assert [item.candidate_evidence[0].skill_name for item in result.results] == ["C", "C++", "C#"]


def test_transferability_output_is_deterministic():
    payload = request()
    stored = [evidence("SQL", "Built SQL reports.")]

    assert build_transferable_skills(payload, stored) == build_transferable_skills(payload, stored)


@pytest.mark.parametrize(
    "payload",
    [
        {"source_role": " ", "target_role": "Analyst"},
        {"source_role": "Analyst", "target_role": " "},
        {"source_role": "Analyst", "target_role": "Engineer", "target_requirements": [""]},
        {"source_role": "Analyst", "target_role": "Engineer", "target_requirements": ["x" * 101]},
        {"source_role": "Analyst", "target_role": "Engineer", "analysis_id": "short"},
        {"source_role": "Analyst", "target_role": "Engineer", "unexpected": "value"},
    ],
)
def test_invalid_transferability_inputs_are_rejected(payload):
    with pytest.raises(ValidationError):
        TransferableSkillsRequest.model_validate(payload)


def test_unknown_and_expired_analysis_identifiers_return_controlled_404(monkeypatch):
    monkeypatch.setattr(main, "analysis_store", AnalysisStore())
    with pytest.raises(HTTPException) as unknown:
        main.transferable_skills(request(analysis_id="x" * 24))
    assert unknown.value.status_code == 404
    assert "not found or has expired" in unknown.value.detail

    now = [100.0]
    store = AnalysisStore(ttl_seconds=30, clock=lambda: now[0])
    analysis_id = store.create([evidence("SQL", "Built SQL reports.")])
    monkeypatch.setattr(main, "analysis_store", store)
    now[0] = 131.0
    with pytest.raises(HTTPException) as expired:
        main.transferable_skills(request(analysis_id=analysis_id))
    assert expired.value.status_code == 404
    assert "not found or has expired" in expired.value.detail


def test_endpoint_declares_typed_request_and_validation_response():
    operation = main.app.openapi()["paths"]["/api/skills/transferability"]["post"]

    assert "422" in operation["responses"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("TransferableSkillsRequest")
