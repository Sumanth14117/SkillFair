import re
import unicodedata

from .models import JobMatchRequest, JobMatchResponse, SkillMatch


def _normalize_skill(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", folded))


def match_job_skills(request: JobMatchRequest) -> JobMatchResponse:
    """Match recruiter requirements to supplied evidence by normalized exact name only."""
    indexed: dict[str, list] = {}
    for skill in request.candidate_skills:
        key = _normalize_skill(skill.skill_name)
        if key:
            indexed.setdefault(key, []).append(skill)

    matched: list[SkillMatch] = []
    missing: list[str] = []
    seen_requirements: set[str] = set()
    for required in request.required_skills:
        display_requirement = required.strip()
        key = _normalize_skill(display_requirement)
        if key in seen_requirements:
            continue
        seen_requirements.add(key)
        candidates = indexed.get(key, [])
        if not candidates:
            missing.append(display_requirement)
            continue
        # Prefer direct evidence when duplicate discovered entries have different evidence types.
        candidate = next((item for item in candidates if item.evidence_type == "explicit"), candidates[0])
        matched.append(SkillMatch(
            required_skill=display_requirement,
            candidate_skill=candidate.skill_name,
            evidence_type=candidate.evidence_type,
            validation_status=candidate.validation_status,
            supporting_excerpt=candidate.supporting_excerpt,
            relevance_explanation=candidate.relevance_explanation,
        ))

    explicit_count = sum(item.evidence_type == "explicit" for item in matched)
    inferred_count = len(matched) - explicit_count
    total = len(seen_requirements)
    coverage = round(len(matched) / total * 100) if total else 0
    return JobMatchResponse(
        job_title=request.job_title.strip(),
        total_required=total,
        explicitly_supported=explicit_count,
        inferred_unverified=inferred_count,
        missing_count=len(missing),
        evidence_coverage_percent=coverage,
        matched_skills=matched,
        missing_skills=missing,
        calculation_method=(
            "Evidence coverage = required skills with an exact normalized name match / unique required skills. "
            "Inferred matches count as unverified coverage and are reported separately. This is not a measure "
            "of competence; review the cited resume evidence and make the hiring decision yourself."
        ),
    )
