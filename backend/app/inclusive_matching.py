from .bias_audit import audit_job
from .matching import _normalize_skill, match_job_skills
from .models import (
    AuditCriterion,
    BiasAuditRequest,
    BiasAuditResponse,
    InclusiveMatchRequest,
    InclusiveMatchRequirement,
    InclusiveMatchResponse,
    JobMatchRequest,
    SkillEvidence,
    TransferabilityEvidence,
    TransferableSkillsRequest,
)
from .transferable_skills import build_transferable_skills


_REVIEW_PROMPTS = [
    "Confirm that each essential requirement is necessary for the role's actual work and describe how it can be demonstrated.",
    "Review cited excerpts in context with the candidate; an extracted skill label is not a measure of proficiency.",
    "Consider whether equivalent experience or an adjacent capability could satisfy the underlying job task, and gather evidence before drawing conclusions.",
]

_DISCLAIMER = (
    "This evidence-led comparison supports recruiter review only. It does not rank or reject candidates, establish competence or qualification, "
    "or prove that a hiring process is fair. Missing evidence is not proof of missing ability. Potential transfers are adjacent signals, not skill equivalence. "
    "The service does not infer sensitive characteristics or demographic information."
)


def _unique_requirements(request: InclusiveMatchRequest) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for importance, skills in (("essential", request.essential_skills), ("preferred", request.preferred_skills)):
        for skill in skills:
            normalized = _normalize_skill(skill)
            key = normalized or f"unmatchable:{skill.casefold()}"
            if key in seen:
                continue
            seen.add(key)
            result.append((skill, importance))
    return result


def build_inclusive_match(
    request: InclusiveMatchRequest,
    candidate_skills: list[SkillEvidence],
    precomputed_audit: BiasAuditResponse | None = None,
) -> InclusiveMatchResponse:
    """Compose exact matching, narrow transferable rules, and advisory job-criteria audit."""
    requirements = _unique_requirements(request)
    normalized_requirements = [skill for skill, _ in requirements]
    exact_result = match_job_skills(
        JobMatchRequest(
            analysis_id=request.analysis_id,
            job_title=request.job_title,
            job_description=request.job_description,
            required_skills=normalized_requirements or ["__no_requirement__"],
        ),
        candidate_skills,
    )
    exact_by_requirement = {_normalize_skill(item.required_skill): item for item in exact_result.matched_skills}
    missing = [skill for skill in normalized_requirements if _normalize_skill(skill) not in exact_by_requirement]

    transfer_by_requirement = {}
    if missing:
        transfer_result = build_transferable_skills(
            TransferableSkillsRequest(
                analysis_id=request.analysis_id,
                source_role="Candidate experience represented in this analysis (past role not linked)",
                target_role=request.job_title,
                target_requirements=missing,
            ),
            candidate_skills,
        )
        transfer_by_requirement = {
            _normalize_skill(item.target_requirement): item
            for item in transfer_result.results
            if item.status == "potential_transfer"
        }

    items: list[InclusiveMatchRequirement] = []
    counts = {"explicitly_evidenced": 0, "inferred_unverified": 0, "potential_transfer": 0, "not_evidenced": 0}
    for skill, importance in requirements:
        key = _normalize_skill(skill)
        exact = exact_by_requirement.get(key)
        if exact:
            status = "explicitly_evidenced" if exact.evidence_type == "explicit" else "inferred_unverified"
            evidence = [TransferabilityEvidence(
                skill_name=exact.candidate_skill,
                evidence_type=exact.evidence_type,
                detection_method=exact.detection_method,
                validation_status=exact.validation_status,
                supporting_excerpt=exact.supporting_excerpt,
                relationship_type="exact_name_match",
                explanation=exact.relevance_explanation,
            )]
            explanation = (
                "The linked analysis contains an exact normalized skill-name match. Review the excerpt in context; "
                "a name match does not establish proficiency or qualification."
            )
        else:
            transfer = transfer_by_requirement.get(key)
            if transfer:
                status = "potential_transfer"
                evidence = transfer.candidate_evidence
                explanation = transfer.explanation
            else:
                status = "not_evidenced"
                evidence = []
                explanation = (
                    "No exact skill-name match or supported curated adjacent-capability relationship was found in this analysis. "
                    "This does not prove the candidate lacks the capability."
                )
        counts[status] += 1
        items.append(InclusiveMatchRequirement(
            skill=skill,
            importance=importance,
            status=status,
            evidence=evidence,
            explanation=explanation,
        ))

    audit_request = BiasAuditRequest(
        job_title=request.job_title,
        job_description=request.job_description,
        requirements=[AuditCriterion(text=skill, importance=importance) for skill, importance in requirements],
    )
    audit = precomputed_audit if precomputed_audit is not None else audit_job(audit_request)

    return InclusiveMatchResponse(
        job_title=request.job_title,
        requirements=items,
        exact_explicit_count=counts["explicitly_evidenced"],
        exact_inferred_count=counts["inferred_unverified"],
        potential_transfer_count=counts["potential_transfer"],
        not_evidenced_count=counts["not_evidenced"],
        bias_audit_findings=audit.findings,
        human_review_prompts=_REVIEW_PROMPTS.copy(),
        disclaimer=_DISCLAIMER,
    )
