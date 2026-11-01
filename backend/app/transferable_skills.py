from .matching import _normalize_skill
from .models import (
    SkillEvidence,
    TransferabilityEvidence,
    TransferabilityResult,
    TransferableSkillsRequest,
    TransferableSkillsResponse,
)


# Deliberately narrow, directional rules. They identify adjacent tasks, not equivalents.
_ADJACENT_CAPABILITIES: dict[str, dict[str, str]] = {
    "sql": {
        "data querying": "SQL querying can be relevant to retrieving and shaping structured data; it does not establish statistical analysis or business interpretation.",
        "data reporting": "SQL experience may support assembling data for reports; it does not establish visualization, interpretation, or reporting ownership.",
    },
    "excel": {
        "spreadsheet analysis": "Spreadsheet work may transfer to organizing and examining tabular information; it does not establish advanced analytics or statistical methods.",
        "data reporting": "Spreadsheet experience may support preparing tabular reports; it does not establish reporting design or interpretation.",
    },
    "customer support": {
        "customer communication": "Customer-support evidence may be relevant to explaining issues and responding to customer needs; target products and responsibilities may differ.",
        "stakeholder communication": "Customer-support communication may transfer to some stakeholder interactions; it does not establish experience with every stakeholder group or context.",
    },
    "technical writing": {
        "documentation": "Technical-writing evidence may be relevant to creating clear documentation; the target audience and document standards may differ.",
        "knowledge base writing": "Technical-writing evidence may transfer to structured knowledge-base content; it does not establish experience with a particular knowledge platform.",
    },
    "project coordination": {
        "project scheduling": "Project-coordination evidence may be relevant to maintaining schedules and follow-ups; it does not establish ownership of project planning or delivery.",
        "stakeholder coordination": "Project-coordination evidence may transfer to organizing stakeholder follow-ups; it does not establish authority over those stakeholders or outcomes.",
    },
}

_DISCLAIMER = (
    "Transferability results are limited signals for human review, not proof of equivalence, proficiency, qualification, or job suitability. "
    "Exact name matches identify a shared skill label only. Adjacent-capability results use a small, explicit rule set and do not establish that one skill replaces another. "
    "The analysis store does not link skills to a specific past role; source_role is recruiter-provided context. "
    "A skill not evidenced in the analysis is not proof that a person lacks it. This service does not infer demographics, rank candidates, or make hiring decisions."
)


def _role_exploration_prompts(source_role: str, target_role: str) -> list[str]:
    return [
        f"Compare the tasks, tools, and work products in {source_role} with the responsibilities of {target_role}; role titles alone do not show which capabilities are shared.",
        f"For each target requirement in {target_role}, look for a concrete task or work sample from prior experience in {source_role} that could provide relevant evidence.",
        f"When reviewing experience from {source_role} for {target_role}, separate directly evidenced skills from adjacent capabilities and note where more evidence may be useful.",
    ]


def build_transferable_skills(
    request: TransferableSkillsRequest,
    candidate_skills: list[SkillEvidence] | None = None,
) -> TransferableSkillsResponse:
    """Compare requested role skills to validated records using exact names and curated adjacent rules."""
    analysis_linked = candidate_skills is not None
    indexed: dict[str, list[SkillEvidence]] = {}
    for skill in candidate_skills or []:
        normalized = _normalize_skill(skill.skill_name)
        if normalized:
            indexed.setdefault(normalized, []).append(skill)

    results: list[TransferabilityResult] = []
    seen_requirements: set[str] = set()
    for requirement in request.target_requirements:
        normalized_requirement = _normalize_skill(requirement)
        dedupe_key = normalized_requirement or f"unmatchable:{requirement.casefold()}"
        if dedupe_key in seen_requirements:
            continue
        seen_requirements.add(dedupe_key)

        prompt = (
            f"Review work and artifacts from {request.source_role} that might demonstrate {requirement} in {request.target_role}; "
            "role titles alone do not establish a transferable capability."
        )
        if not analysis_linked:
            results.append(TransferabilityResult(
                target_requirement=requirement,
                status="not_compared",
                candidate_evidence=[],
                explanation="No candidate analysis was linked. This is a generic role-to-role exploration prompt, not a claim about the candidate.",
                exploration_prompt=prompt,
            ))
            continue

        exact = indexed.get(normalized_requirement, []) if normalized_requirement else []
        if exact:
            selected = next((skill for skill in exact if skill.evidence_type == "explicit"), exact[0])
            results.append(TransferabilityResult(
                target_requirement=requirement,
                status="exact_match",
                candidate_evidence=[TransferabilityEvidence(
                    skill_name=selected.skill_name,
                    evidence_type=selected.evidence_type,
                    detection_method=selected.detection_method,
                    validation_status=selected.validation_status,
                    supporting_excerpt=selected.supporting_excerpt,
                    relationship_type="exact_name_match",
                    explanation="The validated analysis contains this same normalized skill name. A name match alone does not establish proficiency or qualification.",
                )],
                explanation="An exact normalized skill-name match was found in the linked analysis. Review the evidence and validation status; a name match does not establish proficiency, qualification, or suitability.",
            ))
            continue

        related: list[tuple[SkillEvidence, str]] = []
        for skill in candidate_skills or []:
            source_key = _normalize_skill(skill.skill_name)
            explanation = _ADJACENT_CAPABILITIES.get(source_key, {}).get(normalized_requirement)
            if explanation:
                related.append((skill, explanation))
        related.sort(key=lambda item: (item[0].evidence_type != "explicit", _normalize_skill(item[0].skill_name)))

        if related:
            evidence = [TransferabilityEvidence(
                skill_name=skill.skill_name,
                evidence_type=skill.evidence_type,
                detection_method=skill.detection_method,
                validation_status=skill.validation_status,
                supporting_excerpt=skill.supporting_excerpt,
                relationship_type="curated_adjacent_capability",
                explanation=(
                    f"The linked analysis contains this source skill. A curated rule identifies a possible relationship to {requirement}: "
                    f"{relationship} This is an adjacent capability, not an equivalent skill or verified qualification."
                ),
            ) for skill, relationship in related]
            results.append(TransferabilityResult(
                target_requirement=requirement,
                status="potential_transfer",
                candidate_evidence=evidence,
                explanation="One or more explicitly listed adjacent-capability rules may be relevant. The recruiter must review context and the cited evidence; this does not establish equivalence.",
            ))
            continue

        results.append(TransferabilityResult(
            target_requirement=requirement,
            status="not_evidenced",
            candidate_evidence=[],
            explanation="The linked analysis did not establish an exact match or a relationship covered by the curated rules. This does not prove the person lacks the skill.",
            exploration_prompt=prompt,
        ))

    comparison_status = "compared" if analysis_linked and bool(results) else "not_compared"
    prompts = _role_exploration_prompts(request.source_role, request.target_role) if not results else []
    if not request.target_requirements:
        prompts = _role_exploration_prompts(request.source_role, request.target_role)

    return TransferableSkillsResponse(
        source_role=request.source_role,
        target_role=request.target_role,
        analysis_linked=analysis_linked,
        candidate_comparison_status=comparison_status,
        results=results,
        exploration_prompts=prompts,
        disclaimer=_DISCLAIMER,
    )
