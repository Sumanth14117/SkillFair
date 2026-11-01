import pytest
from pydantic import ValidationError
from fastapi import HTTPException

from app import main
from app.main import app, match_job
from app.matching import match_job_skills
from app.models import JobMatchRequest, SkillEvidence, SkillMatch
from app.provenance import AnalysisStore

TEST_ANALYSIS_ID = "test-analysis-identifier-1234567890"


def evidence(name: str, excerpt: str, kind: str = "explicit") -> dict[str, str]:
    return {
        "skill_name": name,
        "evidence_type": kind,
        "supporting_excerpt": excerpt,
        "relevance_explanation": "Skill listed in the previously analyzed resume.",
        "validation_status": "evidence_found" if kind == "explicit" else "inference_unverified",
    }


def test_job_matching_uses_exact_normalized_names_and_preserves_evidence():
    request = JobMatchRequest(
        analysis_id=TEST_ANALYSIS_ID,
        job_title="Data Analyst",
        job_description="Build reports and partner with teams.",
        required_skills=[" SQL ", "DATA-VISUALIZATION", "Stakeholder communication", "SQL"],
    )
    candidate_skills = [
        evidence("sql", "Used SQL to query reporting tables."),
        evidence("Data Visualization", "Created data visualization dashboards.", "inferred"),
    ]

    result = match_job_skills(request, [SkillEvidence.model_validate(item) for item in candidate_skills])

    assert result.job_title == "Data Analyst"
    assert result.total_required == 3
    assert result.explicitly_supported == 1
    assert result.inferred_unverified == 1
    assert result.missing_count == 1
    assert result.evidence_coverage_percent == 67
    assert result.matched_skills[0].supporting_excerpt == "Used SQL to query reporting tables."
    assert result.matched_skills[1].validation_status == "inference_unverified"
    assert result.missing_skills == ["Stakeholder communication"]
    assert "not a measure of competence" in result.calculation_method


def test_redacted_provenance_evidence_remains_usable_by_job_matching(monkeypatch):
    store = AnalysisStore()
    source_payload = evidence("Python", "Built Python tools; sam@example.com.")
    source_payload["relevance_explanation"] = "Contact sam@example.com to discuss Python experience."
    source = SkillEvidence.model_validate(source_payload)
    analysis_id = store.create([source])
    monkeypatch.setattr(main, "analysis_store", store)
    result = match_job(JobMatchRequest(
        analysis_id=analysis_id, job_title="Developer", job_description="Build tools.", required_skills=["Python"]
    ))
    assert result.matched_skills[0].supporting_excerpt == "Built Python tools; [REDACTED EMAIL]."
    assert result.matched_skills[0].relevance_explanation == "Contact [REDACTED EMAIL] to discuss Python experience."
    assert "sam@example.com" not in result.model_dump_json()


def test_job_matching_does_not_use_substring_or_semantic_guessing():
    request = JobMatchRequest(
        analysis_id=TEST_ANALYSIS_ID,
        job_title="Engineer",
        job_description="Role details.",
        required_skills=["Java", "Data analysis"],
    )

    result = match_job_skills(request, [SkillEvidence.model_validate(evidence("JavaScript", "Built JavaScript applications."))])

    assert result.matched_skills == []
    assert result.missing_skills == ["Java", "Data analysis"]
    assert result.evidence_coverage_percent == 0


@pytest.mark.parametrize(
    ("required", "candidate"),
    [("C", "C++"), ("C", "C#"), ("C++", "C#")],
)
def test_programming_language_symbols_do_not_collide(required, candidate):
    result = match_job_skills(JobMatchRequest(
        analysis_id=TEST_ANALYSIS_ID,
        job_title="Developer",
        job_description="Build software.",
        required_skills=[required],
    ), [SkillEvidence.model_validate(evidence(candidate, f"Used {candidate} professionally."))])

    assert result.matched_skills == []
    assert result.missing_skills == [required]
    assert result.evidence_coverage_percent == 0


@pytest.mark.parametrize("skill", ["C", "C++", "C#", "Python", "SQL", "JavaScript"])
def test_exact_programming_skill_names_match(skill):
    result = match_job_skills(JobMatchRequest(
        analysis_id=TEST_ANALYSIS_ID,
        job_title="Developer",
        job_description="Build software.",
        required_skills=[skill],
    ), [SkillEvidence.model_validate(evidence(skill, f"Used {skill} professionally."))])

    assert len(result.matched_skills) == 1
    assert result.matched_skills[0].required_skill == skill
    assert result.missing_skills == []
    assert result.evidence_coverage_percent == 100


@pytest.mark.parametrize(
    ("required", "candidate"),
    [
        (" c ", "C"),
        ("c ++", "C++"),
        ("C #", "c#"),
        ("  pYtHoN  ", "Python"),
        (" JavaScript ", "javascript"),
        ("DATA-VISUALIZATION", "Data Visualization"),
    ],
)
def test_case_and_whitespace_variations_match_without_fuzzy_equivalence(required, candidate):
    result = match_job_skills(JobMatchRequest(
        analysis_id=TEST_ANALYSIS_ID,
        job_title="Developer",
        job_description="Build software.",
        required_skills=[required],
    ), [SkillEvidence.model_validate(evidence(candidate, f"Experience with {candidate}."))])

    assert len(result.matched_skills) == 1
    assert result.missing_skills == []


def test_job_matching_prefers_explicit_evidence_for_duplicate_candidate_skill():
    request = JobMatchRequest(
        analysis_id=TEST_ANALYSIS_ID,
        job_title="Analyst",
        job_description="Role details.",
        required_skills=["Data analysis"],
    )
    candidate_skills = [
        evidence("Data Analysis", "Led analytics projects.", "inferred"),
        evidence("data analysis", "Performed data analysis using SQL."),
    ]

    result = match_job_skills(request, [SkillEvidence.model_validate(item) for item in candidate_skills])

    assert result.matched_skills[0].evidence_type == "explicit"
    assert result.matched_skills[0].supporting_excerpt == "Performed data analysis using SQL."


def test_job_matching_validates_job_fields_and_requires_analysis_identifier():
    with pytest.raises(ValidationError):
        JobMatchRequest(analysis_id=TEST_ANALYSIS_ID, job_title="  ", job_description="Role details", required_skills=["SQL"])
    with pytest.raises(ValidationError):
        JobMatchRequest(analysis_id=TEST_ANALYSIS_ID, job_title="Analyst", job_description="Role details", required_skills=[""])
    with pytest.raises(ValidationError):
        JobMatchRequest(job_title="Analyst", job_description="Role details", required_skills=["SQL"])
    with pytest.raises(ValidationError):
        JobMatchRequest(analysis_id="short", job_title="Analyst", job_description="Role details", required_skills=["SQL"])


@pytest.mark.parametrize(
    ("evidence_type", "validation_status"),
    [("explicit", "evidence_found"), ("inferred", "inference_unverified")],
)
def test_skill_evidence_accepts_contract_valid_status_pairs(evidence_type, validation_status):
    skill = SkillEvidence.model_validate(evidence("Skill", "Skill excerpt", evidence_type) | {
        "validation_status": validation_status,
    })

    assert skill.evidence_type == evidence_type
    assert skill.validation_status == validation_status


@pytest.mark.parametrize(
    ("evidence_type", "validation_status"),
    [
        ("explicit", "inference_unverified"),
        ("inferred", "evidence_found"),
        ("suggested", "evidence_found"),
        ("explicit", "pending"),
    ],
)
def test_skill_evidence_rejects_contradictory_or_unsupported_status_pairs(evidence_type, validation_status):
    with pytest.raises(ValidationError):
        SkillEvidence.model_validate(evidence("Skill", "Skill excerpt", evidence_type) | {
            "validation_status": validation_status,
        })


def test_skill_match_model_enforces_the_same_evidence_status_contract():
    with pytest.raises(ValidationError):
        SkillMatch(
            required_skill="Python",
            candidate_skill="Python",
            evidence_type="inferred",
            validation_status="evidence_found",
            supporting_excerpt="Used Python",
            relevance_explanation="Mentioned directly.",
        )


def test_job_matching_rejects_client_supplied_evidence_by_422_request_contract():
    payload = {
        "analysis_id": TEST_ANALYSIS_ID,
        "job_title": "Developer",
        "job_description": "Build software.",
        "required_skills": ["Python"],
        "candidate_skills": [{
            "skill_name": "Python",
            "evidence_type": "inferred",
            "supporting_excerpt": "Used Python",
            "relevance_explanation": "Mentioned in a project description.",
            "validation_status": "evidence_found",
        }],
    }

    with pytest.raises(ValidationError):
        JobMatchRequest.model_validate(payload)
    # FastAPI validates the typed request body before calling the endpoint and
    # publishes its standard 422 response for invalid request data.
    operation = app.openapi()["paths"]["/api/jobs/match"]["post"]
    assert "422" in operation["responses"]


def test_valid_analysis_provenance_supplies_matching_evidence(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([SkillEvidence.model_validate(evidence("Python", "Built Python services."))])
    monkeypatch.setattr(main, "analysis_store", store)
    request = JobMatchRequest(
        analysis_id=analysis_id,
        job_title="Developer",
        job_description="Build software.",
        required_skills=["Python", "SQL"],
    )

    result = match_job(request)

    assert result.matched_skills[0].supporting_excerpt == "Built Python services."
    assert result.missing_skills == ["SQL"]


def test_unknown_analysis_identifier_returns_controlled_not_found(monkeypatch):
    monkeypatch.setattr(main, "analysis_store", AnalysisStore())
    request = JobMatchRequest(
        analysis_id=TEST_ANALYSIS_ID,
        job_title="Developer",
        job_description="Build software.",
        required_skills=["Python"],
    )

    with pytest.raises(HTTPException) as missing:
        match_job(request)

    assert missing.value.status_code == 404
    assert "not found or has expired" in missing.value.detail


def test_expired_analysis_identifier_returns_controlled_not_found(monkeypatch):
    now = [100.0]
    store = AnalysisStore(ttl_seconds=30, clock=lambda: now[0])
    analysis_id = store.create([SkillEvidence.model_validate(evidence("Python", "Built Python services."))])
    monkeypatch.setattr(main, "analysis_store", store)
    now[0] = 131.0
    request = JobMatchRequest(
        analysis_id=analysis_id,
        job_title="Developer",
        job_description="Build software.",
        required_skills=["Python"],
    )

    with pytest.raises(HTTPException) as expired:
        match_job(request)

    assert expired.value.status_code == 404
    assert "not found or has expired" in expired.value.detail


def test_client_supplied_evidence_is_rejected_and_cannot_override_stored_skills(monkeypatch):
    store = AnalysisStore()
    analysis_id = store.create([SkillEvidence.model_validate(evidence("Python", "Built Python services."))])
    monkeypatch.setattr(main, "analysis_store", store)
    tampered_request = {
        "analysis_id": analysis_id,
        "job_title": "Developer",
        "job_description": "Build software.",
        "required_skills": ["Python", "SQL"],
        "candidate_skills": [evidence("SQL", "Forged SQL evidence.")],
    }

    with pytest.raises(ValidationError):
        JobMatchRequest.model_validate(tampered_request)

    result = match_job(JobMatchRequest.model_validate({key: value for key, value in tampered_request.items() if key != "candidate_skills"}))
    assert [item.candidate_skill for item in result.matched_skills] == ["Python"]
    assert result.missing_skills == ["SQL"]


def test_analysis_store_uses_unique_ids_and_enforces_capacity():
    store = AnalysisStore(max_entries=2)
    skill = SkillEvidence.model_validate(evidence("Python", "Built Python services."))

    first = store.create([skill])
    second = store.create([skill])
    third = store.create([skill])

    assert first != second != third
    assert store.resolve(first) is None
    assert store.resolve(second) is not None
    assert store.resolve(third) is not None
