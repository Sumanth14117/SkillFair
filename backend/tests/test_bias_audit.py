import pytest
from pydantic import ValidationError

from app.bias_audit import audit_job
from app.main import app, audit_job_description
from app.models import AuditCriterion, BiasAuditRequest


def audit(description: str = "", requirements: list[AuditCriterion] | None = None):
    return audit_job(BiasAuditRequest(
        job_title="Product Analyst",
        job_description=description,
        requirements=requirements or [],
    ))


def test_audit_flags_hard_credential_requirement_as_potential_concern():
    result = audit(requirements=[AuditCriterion(text="Bachelor's degree required")])

    finding = result.findings[0]
    assert finding.rule_id == "BA-CREDENTIAL-001"
    assert finding.category == "credential_requirement"
    assert finding.priority == "medium"
    assert finding.triggering_phrase == "Bachelor's degree required"
    assert finding.criterion_importance == "essential"
    assert finding.status == "potential_concern_for_human_review"
    assert finding.human_review_required is True
    assert "equivalent" in finding.suggested_revision_or_action


@pytest.mark.parametrize(
    "criterion",
    [
        "A bachelor's degree is preferred",
        "The team values certifications",
        "A professional license is legally required by law",
        "Bachelor's degree required or equivalent experience",
    ],
)
def test_audit_does_not_flag_credential_mentions_without_a_reviewable_hard_gate(criterion):
    assert audit(requirements=[AuditCriterion(text=criterion)]).findings == []


@pytest.mark.parametrize(
    ("phrase", "rule_suffix"),
    [
        ("native English speaker", "NATIVE-SPEAKER"),
        ("digital native", "DIGITAL-NATIVE"),
        ("young and energetic", "YOUTH-CODED"),
        ("recent graduates only", "RECENT-GRADUATE-ONLY"),
        ("culture fit", "CULTURE-FIT"),
        ("mother tongue", "MOTHER-TONGUE"),
    ],
)
def test_audit_flags_specific_potentially_exclusionary_wording(phrase, rule_suffix):
    result = audit(requirements=[AuditCriterion(text=f"We are looking for a {phrase}.")])

    finding = next(item for item in result.findings if item.category == "potentially_exclusionary_wording")
    assert finding.rule_id == f"BA-WORDING-{rule_suffix}-001"
    assert finding.triggering_phrase.casefold() == phrase.casefold()
    assert finding.human_review_required is True
    assert finding.suggested_revision_or_action


@pytest.mark.parametrize(
    "phrase",
    [
        "Excellent communication skills",
        "Strong written and verbal communication skills",
        "Team player",
        "Self-starter",
        "Detail-oriented",
        "Good attitude",
        "Strong work ethic",
        "Highly motivated",
        "Strong problem-solving skills",
        "Proven track record",
    ],
)
def test_audit_flags_vague_non_measurable_skill_phrases(phrase):
    result = audit(requirements=[AuditCriterion(text=phrase)])

    finding = next(item for item in result.findings if item.category == "vague_requirement")
    assert finding.rule_id == "BA-VAGUE-SKILL-001"
    assert finding.triggering_phrase.casefold() == phrase.casefold()
    assert "observable" in finding.explanation
    assert finding.human_review_required is True


@pytest.mark.parametrize(
    ("criterion", "should_flag"),
    [
        (AuditCriterion(text="Minimum of 10 years of experience"), True),
        (AuditCriterion(text="10+ years of experience"), True),
        (AuditCriterion(text="15 years experience required", importance="essential"), True),
        (AuditCriterion(text="5 years of experience required"), False),
        (AuditCriterion(text="10 years of experience"), False),
        (AuditCriterion(text="10+ years of experience", importance="preferred"), False),
    ],
)
def test_experience_audit_requires_a_high_threshold_and_hard_gate(criterion, should_flag):
    result = audit(requirements=[criterion])
    findings = [item for item in result.findings if item.category == "experience_threshold"]

    assert bool(findings) is should_flag
    if findings:
        assert findings[0].rule_id == "BA-EXPERIENCE-10Y-001"
        assert findings[0].priority == "high"
        assert findings[0].human_review_required is True


@pytest.mark.parametrize(
    "description",
    [
        "Minimum 8 years of continuous professional experience.",
        "No employment gaps during the last five years.",
        "No career breaks permitted.",
        "The role requires an uninterrupted employment history.",
    ],
)
def test_audit_flags_explicit_employment_continuity_restrictions(description):
    result = audit(description)

    finding = next(item for item in result.findings if item.rule_id == "BA-EXPERIENCE-CONTINUITY-001")
    assert finding.category == "experience_threshold"
    assert finding.priority == "medium"
    assert finding.human_review_required is True
    assert "career-returning candidates" in finding.explanation
    assert "relevant skills" in finding.suggested_revision_or_action


@pytest.mark.parametrize(
    "description",
    [
        "Minimum 8 years of professional experience.",
        "Three years of experience required.",
        "Career breaks are acceptable and will not disqualify applicants.",
    ],
)
def test_audit_does_not_flag_ordinary_experience_or_permissive_break_language(description):
    result = audit(description)

    assert not any(item.rule_id == "BA-EXPERIENCE-CONTINUITY-001" for item in result.findings)


def test_description_headings_distinguish_essential_from_preferred():
    result = audit(
        "Required qualifications:\nBachelor's degree required\n"
        "Preferred qualifications:\n10+ years of experience"
    )

    assert result.finding_count == 1
    assert result.findings[0].criterion_importance == "essential"
    assert result.findings[0].rule_id == "BA-CREDENTIAL-001"
    assert result.criteria_reviewed == 2


def test_empty_and_malformed_audit_inputs_are_rejected():
    with pytest.raises(ValidationError):
        BiasAuditRequest()
    with pytest.raises(ValidationError):
        BiasAuditRequest(requirements=[{"text": "   "}])
    with pytest.raises(ValidationError):
        BiasAuditRequest(requirements=[{"text": "SQL", "importance": "mandatory"}])
    with pytest.raises(ValidationError):
        BiasAuditRequest(requirements=[{"text": "SQL", "unexpected": "value"}])


def test_no_findings_response_is_cautious_and_has_stable_structure():
    result = audit(
        "Bachelor's degree preferred. Three years of experience."
    ).model_dump()

    assert set(result) == {
        "job_title", "criteria_reviewed", "finding_count", "findings",
        "human_review_required", "disclaimer",
    }
    assert result["criteria_reviewed"] == 2
    assert result["finding_count"] == 0
    assert result["findings"] == []
    assert result["human_review_required"] is True
    assert "do not establish fairness" in result["disclaimer"]
    assert "legal compliance" in result["disclaimer"]


def test_audit_endpoint_uses_typed_request_and_declares_validation_response():
    result = audit_job_description(BiasAuditRequest(requirements=[AuditCriterion(text="Team player")]))

    operation = app.openapi()["paths"]["/api/jobs/audit"]["post"]
    assert "422" in operation["responses"]
    assert result.finding_count == 1
