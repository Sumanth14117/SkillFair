import pytest
from pydantic import ValidationError

from app.main import match_job
from app.models import JobMatchRequest


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
        job_title="Data Analyst",
        job_description="Build reports and partner with teams.",
        required_skills=[" SQL ", "DATA-VISUALIZATION", "Stakeholder communication", "SQL"],
        candidate_skills=[
            evidence("sql", "Used SQL to query reporting tables."),
            evidence("Data Visualization", "Created data visualization dashboards.", "inferred"),
        ],
    )

    result = match_job(request)

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


def test_job_matching_does_not_use_substring_or_semantic_guessing():
    request = JobMatchRequest(
        job_title="Engineer",
        job_description="Role details.",
        required_skills=["Java", "Data analysis"],
        candidate_skills=[evidence("JavaScript", "Built JavaScript applications.")],
    )

    result = match_job(request)

    assert result.matched_skills == []
    assert result.missing_skills == ["Java", "Data analysis"]
    assert result.evidence_coverage_percent == 0


def test_job_matching_prefers_explicit_evidence_for_duplicate_candidate_skill():
    request = JobMatchRequest(
        job_title="Analyst",
        job_description="Role details.",
        required_skills=["Data analysis"],
        candidate_skills=[
            evidence("Data Analysis", "Led analytics projects.", "inferred"),
            evidence("data analysis", "Performed data analysis using SQL."),
        ],
    )

    result = match_job(request)

    assert result.matched_skills[0].evidence_type == "explicit"
    assert result.matched_skills[0].supporting_excerpt == "Performed data analysis using SQL."


def test_job_matching_validates_job_fields_and_candidate_skill_count():
    with pytest.raises(ValidationError):
        JobMatchRequest(job_title="  ", job_description="Role details", required_skills=["SQL"], candidate_skills=[])
    with pytest.raises(ValidationError):
        JobMatchRequest(job_title="Analyst", job_description="Role details", required_skills=[""], candidate_skills=[])
    with pytest.raises(ValidationError):
        JobMatchRequest(
            job_title="Analyst",
            job_description="Role details",
            required_skills=["SQL"],
            candidate_skills=[evidence("SQL", "SQL")] * 61,
        )
