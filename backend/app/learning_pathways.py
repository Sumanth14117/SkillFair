from .matching import _normalize_skill
from .models import (
    LearningActivity,
    LearningEvidenceStatus,
    LearningPathwayItem,
    LearningPathwaysRequest,
    LearningPathwaysResponse,
    SkillEvidence,
)


_DISCLAIMER = (
    "Learning activities are deterministic suggestions, not courses, certifications, or evidence of completed training. "
    "Resume evidence reflects only the linked analysis. A requirement not evidenced there does not mean the person lacks the skill. "
    "Use these pathways as optional guidance; they do not rank candidates or make hiring decisions."
)


def _activities(skill: str, status: LearningEvidenceStatus) -> list[LearningActivity]:
    if status == "explicitly_evidenced":
        return [LearningActivity(
            activity_type="demonstration",
            title=f"Extend your {skill} work sample",
            description=f"Prepare a concise example of using {skill} on a realistic task, including your approach, decisions, and result.",
            relevance=f"This optional activity can help make existing resume evidence for {skill} easier to review; it is not a qualification or verification by itself.",
        )]
    if status == "inferred_unverified":
        return [
            LearningActivity(
                activity_type="practice_exercise",
                title=f"Practice {skill} in a focused task",
                description=f"Choose a small, realistic task using {skill}; record the steps you take and the result you produce.",
                relevance=f"A practical exercise can help you assess and strengthen {skill}, which is currently inferred rather than explicitly evidenced in the analysis.",
            ),
            LearningActivity(
                activity_type="demonstration",
                title=f"Create evidence of {skill}",
                description=f"Prepare a short walkthrough or work sample showing how you applied {skill} and what you contributed.",
                relevance=f"A concrete example may help a reviewer assess {skill}; the current inferred signal is not verified capability.",
            ),
        ]
    return [
        LearningActivity(
            activity_type="practice_exercise",
            title=f"Build a foundation in {skill}",
            description=f"Break {skill} into a small, role-relevant task. Practice it, note what you learned, and repeat with a slightly harder variation.",
            relevance=f"Focused practice offers a starting point for developing {skill}; the linked analysis does not contain evidence for this requirement.",
        ),
        LearningActivity(
            activity_type="project_prompt",
            title=f"Apply {skill} in a small project",
            description=f"Create a self-contained project that uses {skill} to solve a clearly defined problem. Document the approach and outcome.",
            relevance=f"A project can provide a practical way to build and demonstrate {skill}; it does not establish that the skill is already held.",
        ),
        LearningActivity(
            activity_type="demonstration",
            title=f"Prepare a {skill} work sample",
            description=f"Save an artifact or walkthrough from your practice or project and explain the decisions involved.",
            relevance=f"A work sample gives a reviewer something concrete to discuss about {skill}; it is not a certification or proof of proficiency on its own.",
        ),
    ]


def build_learning_pathways(
    request: LearningPathwaysRequest,
    candidate_skills: list[SkillEvidence] | None = None,
) -> LearningPathwaysResponse:
    """Build deterministic activities using only validated server-side skill records."""
    analysis_linked = candidate_skills is not None
    indexed: dict[str, list[SkillEvidence]] = {}
    for evidence in candidate_skills or []:
        key = _normalize_skill(evidence.skill_name)
        if key:
            indexed.setdefault(key, []).append(evidence)

    pathways: list[LearningPathwayItem] = []
    seen: set[str] = set()
    for required_skill in request.required_skills:
        normalized = _normalize_skill(required_skill)
        key = normalized or f"unmatchable:{required_skill.casefold()}"
        if key in seen:
            continue
        seen.add(key)

        evidence = None
        if analysis_linked:
            candidates = indexed.get(normalized, []) if normalized else []
            evidence = next((item for item in candidates if item.evidence_type == "explicit"), None)
            if evidence is None and candidates:
                evidence = candidates[0]

        if not analysis_linked:
            status: LearningEvidenceStatus = "not_compared"
        elif evidence is None:
            status = "not_evidenced_in_analysis"
        elif evidence.evidence_type == "explicit":
            status = "explicitly_evidenced"
        else:
            status = "inferred_unverified"

        pathways.append(LearningPathwayItem(
            required_skill=required_skill,
            evidence_status=status,
            evidenced_skill=evidence.skill_name if evidence else None,
            evidence_type=evidence.evidence_type if evidence else None,
            validation_status=evidence.validation_status if evidence else None,
            detection_method=evidence.detection_method if evidence else None,
            activities=_activities(required_skill, status),
        ))

    return LearningPathwaysResponse(
        job_title=request.job_title,
        analysis_linked=analysis_linked,
        requirement_count=len(pathways),
        pathways=pathways,
        disclaimer=_DISCLAIMER,
    )
