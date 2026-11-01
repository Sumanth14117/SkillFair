"""Owner-scoped persistence helpers for analyses and Career Orchestrator runs."""

from __future__ import annotations

from datetime import datetime, timezone
from secrets import token_urlsafe
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .career_orchestrator import (
    CareerWorkflowReviewError,
    _WORKFLOW_DISCLAIMERS,
    _WorkflowRecord,
    _build_review_transition,
)
from .db_models import AnalysisSkill, CareerWorkflow, ResumeAnalysis, WorkflowReview
from .models import (
    CareerHumanReviewRequest,
    CareerHumanReviewState,
    CareerLearningStage,
    CareerWorkflowListItem,
    CareerWorkflowRunResponse,
    CareerWorkflowStage,
    BiasAuditResponse,
    InclusiveMatchResponse,
    MarketIntelligenceResponse,
    PersistedAnalysisResponse,
    SkillEvidence,
    SkillPassportSummary,
)
from .privacy import redact_skill_evidence


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_skills(skills: list[SkillEvidence]) -> list[SkillEvidence]:
    return [SkillEvidence.model_validate(redact_skill_evidence(skill).model_dump()) for skill in skills]


def persist_analysis(
    db: Session,
    owner_user_id: UUID,
    analysis_mode: str,
    skills: list[SkillEvidence],
) -> PersistedAnalysisResponse:
    """Persist only validated/redacted skill records; never resume text or files."""
    safe_skills = _safe_skills(skills)
    analysis = ResumeAnalysis(
        public_id=token_urlsafe(32),
        owner_user_id=owner_user_id,
        analysis_mode=analysis_mode,
        created_at=_now(),
        expires_at=None,
        deleted_at=None,
    )
    db.add(analysis)
    db.flush()
    db.add_all([
        AnalysisSkill(
            analysis_id=analysis.id,
            owner_user_id=owner_user_id,
            skill_name=skill.skill_name,
            evidence_type=skill.evidence_type,
            detection_method=skill.detection_method,
            validation_status=skill.validation_status,
            supporting_excerpt=skill.supporting_excerpt,
            relevance_explanation=skill.relevance_explanation,
        )
        for skill in safe_skills
    ])
    db.commit()
    return PersistedAnalysisResponse(
        analysis_id=analysis.public_id,
        analysis_mode=analysis.analysis_mode,
        created_at=analysis.created_at,
        skills=safe_skills,
    )


def _analysis_row(db: Session, owner_user_id: UUID, public_id: str) -> ResumeAnalysis | None:
    now = _now()
    row = db.scalar(select(ResumeAnalysis).where(
        ResumeAnalysis.public_id == public_id,
        ResumeAnalysis.owner_user_id == owner_user_id,
        ResumeAnalysis.deleted_at.is_(None),
        (ResumeAnalysis.expires_at.is_(None)) | (ResumeAnalysis.expires_at > now),
    ))
    return row


def resolve_analysis(
    db: Session,
    owner_user_id: UUID,
    public_id: str,
) -> tuple[ResumeAnalysis, list[SkillEvidence]] | None:
    row = _analysis_row(db, owner_user_id, public_id)
    if row is None:
        return None
    records = db.scalars(select(AnalysisSkill).where(
        AnalysisSkill.analysis_id == row.id,
        AnalysisSkill.owner_user_id == owner_user_id,
    ).order_by(AnalysisSkill.created_at, AnalysisSkill.id)).all()
    skills = [
        SkillEvidence.model_validate(redact_skill_evidence(SkillEvidence(
            skill_name=item.skill_name,
            evidence_type=item.evidence_type,
            detection_method=item.detection_method,
            validation_status=item.validation_status,
            supporting_excerpt=item.supporting_excerpt,
            relevance_explanation=item.relevance_explanation,
        )))
        for item in records
    ]
    return row, skills


def latest_analysis(db: Session, owner_user_id: UUID) -> PersistedAnalysisResponse | None:
    row = db.scalar(select(ResumeAnalysis).where(
        ResumeAnalysis.owner_user_id == owner_user_id,
        ResumeAnalysis.deleted_at.is_(None),
        (ResumeAnalysis.expires_at.is_(None)) | (ResumeAnalysis.expires_at > _now()),
    ).order_by(ResumeAnalysis.created_at.desc(), ResumeAnalysis.id.desc()).limit(1))
    if row is None:
        return None
    resolved = resolve_analysis(db, owner_user_id, row.public_id)
    if resolved is None:
        return None
    _, skills = resolved
    return PersistedAnalysisResponse(
        analysis_id=row.public_id,
        analysis_mode=row.analysis_mode,
        created_at=row.created_at,
        skills=skills,
    )


def _workflow_review(db: Session, row: CareerWorkflow) -> WorkflowReview | None:
    return db.scalar(select(WorkflowReview).where(
        WorkflowReview.workflow_id == row.id,
        WorkflowReview.owner_user_id == row.owner_user_id,
    ))


def _linked_analysis(db: Session, analysis_id: UUID, owner_user_id: UUID) -> ResumeAnalysis | None:
    """Resolve a workflow's analysis only within its authenticated owner scope."""
    return db.scalar(select(ResumeAnalysis).where(
        ResumeAnalysis.id == analysis_id,
        ResumeAnalysis.owner_user_id == owner_user_id,
    ))


def _typed_snapshot(value: Any, model: type[Any]) -> Any:
    return model.model_validate(value)


def workflow_response(db: Session, row: CareerWorkflow) -> CareerWorkflowRunResponse:
    review = _workflow_review(db, row)
    if review is None:
        raise RuntimeError("Workflow review state is missing")
    human_review = CareerHumanReviewState(
        status="recorded" if review.decision else "pending",
        decision=review.decision,
        reviewable_requirement_ids=row.reviewable_requirement_ids,
        reviewed_requirement_ids=review.reviewed_requirement_ids or [],
        # Review notes are intentionally not persisted by the established schema.
        review_notes=None,
        attested_at=review.review_completed_at,
        reviewer_identity_verified=False,
    )
    return CareerWorkflowRunResponse(
        run_id=row.public_id,
        started_at=row.created_at,
        workflow_status=row.workflow_status,
        stages=[_typed_snapshot(item, CareerWorkflowStage) for item in row.stages_snapshot],
        skill_passport=_typed_snapshot(row.skill_passport_snapshot, SkillPassportSummary),
        market_intelligence=_typed_snapshot(row.market_intelligence_snapshot, MarketIntelligenceResponse),
        job_audit=_typed_snapshot(row.job_audit_snapshot, BiasAuditResponse),
        inclusive_matching=_typed_snapshot(row.inclusive_matching_snapshot, InclusiveMatchResponse),
        human_review=human_review,
        learning_pathways=_typed_snapshot(row.learning_pathways_snapshot, CareerLearningStage),
        workflow_summary=row.workflow_summary,
        disclaimers=list(_WORKFLOW_DISCLAIMERS),
    )


def persist_workflow(
    db: Session,
    owner_user_id: UUID,
    analysis: ResumeAnalysis,
    request: Any,
    response: CareerWorkflowRunResponse,
) -> CareerWorkflowRunResponse:
    row = CareerWorkflow(
        public_id=response.run_id,
        owner_user_id=owner_user_id,
        analysis_id=analysis.id,
        job_title=request.job_title,
        job_description=request.job_description,
        geography=request.geography,
        essential_skills=list(request.essential_skills),
        preferred_skills=list(request.preferred_skills),
        workflow_status=response.workflow_status,
        reviewable_requirement_ids=list(response.human_review.reviewable_requirement_ids),
        stages_snapshot=[item.model_dump(mode="json") for item in response.stages],
        skill_passport_snapshot=response.skill_passport.model_dump(mode="json"),
        market_intelligence_snapshot=response.market_intelligence.model_dump(mode="json"),
        job_audit_snapshot=response.job_audit.model_dump(mode="json"),
        inclusive_matching_snapshot=response.inclusive_matching.model_dump(mode="json"),
        learning_pathways_snapshot=response.learning_pathways.model_dump(mode="json"),
        workflow_summary=response.workflow_summary,
        version=1,
        created_at=response.started_at,
        updated_at=response.started_at,
        expires_at=None,
    )
    db.add(row)
    db.flush()
    db.add(WorkflowReview(
        workflow_id=row.id,
        owner_user_id=owner_user_id,
        decision=None,
        reviewed_requirement_ids=[],
        review_started_at=None,
        review_completed_at=None,
    ))
    db.commit()
    return response


def get_workflow(db: Session, owner_user_id: UUID, public_id: str) -> CareerWorkflowRunResponse | None:
    row = db.scalar(select(CareerWorkflow).where(
        CareerWorkflow.public_id == public_id,
        CareerWorkflow.owner_user_id == owner_user_id,
        (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > _now()),
    ))
    return workflow_response(db, row) if row is not None else None


def list_workflows(db: Session, owner_user_id: UUID, limit: int = 30) -> list[CareerWorkflowListItem]:
    rows = db.scalars(select(CareerWorkflow).where(
        CareerWorkflow.owner_user_id == owner_user_id,
        (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > _now()),
    ).order_by(CareerWorkflow.created_at.desc(), CareerWorkflow.id.desc()).limit(limit)).all()
    items = []
    for row in rows:
        response = workflow_response(db, row)
        items.append(CareerWorkflowListItem(
            run_id=row.public_id,
            job_title=row.job_title,
            job_description=row.job_description,
            geography=row.geography,
            essential_skills=list(row.essential_skills),
            preferred_skills=list(row.preferred_skills),
            workflow=response,
        ))
    return items


def record_workflow_review_start(db: Session, owner_user_id: UUID, public_id: str, at: datetime | None = None) -> bool | None:
    row = db.scalar(select(CareerWorkflow).where(
        CareerWorkflow.public_id == public_id,
        CareerWorkflow.owner_user_id == owner_user_id,
        (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > _now()),
    ).with_for_update())
    if row is None:
        return None
    review = _workflow_review(db, row)
    if review is None:
        return None
    if review.decision is not None:
        return False
    if review.review_started_at is not None:
        return False
    review.review_started_at = at or _now()
    db.commit()
    return True


def apply_workflow_review(
    db: Session,
    owner_user_id: UUID,
    public_id: str,
    request: CareerHumanReviewRequest,
    reviewer_user_id: UUID,
) -> CareerWorkflowRunResponse | None:
    """Lock, validate and persist a single cross-worker-safe review transition."""
    if reviewer_user_id != owner_user_id:
        return None
    row = db.scalar(select(CareerWorkflow).where(
        CareerWorkflow.public_id == public_id,
        CareerWorkflow.owner_user_id == owner_user_id,
        (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > _now()),
    ).with_for_update())
    if row is None:
        return None
    review = db.scalar(select(WorkflowReview).where(
        WorkflowReview.workflow_id == row.id,
        WorkflowReview.owner_user_id == owner_user_id,
    ).with_for_update())
    if review is None:
        return None
    current = workflow_response(db, row)
    if review.decision is not None:
        if review.decision == request.decision and set(review.reviewed_requirement_ids) == set(request.reviewed_requirement_ids):
            db.rollback()
            return current
        raise CareerWorkflowReviewError("A review decision has already been recorded for this workflow.")

    analysis = _linked_analysis(db, row.analysis_id, owner_user_id)
    if analysis is None or analysis.owner_user_id != owner_user_id or analysis.deleted_at is not None:
        raise CareerWorkflowReviewError("The linked skills analysis is no longer available.")
    resolved = resolve_analysis(db, owner_user_id, analysis.public_id)
    if resolved is None:
        raise CareerWorkflowReviewError("The linked skills analysis is no longer available.")
    _, skills = resolved
    from .models import CareerOrchestrationRequest

    stored_request = CareerOrchestrationRequest(
        analysis_id=analysis.public_id,
        job_title=row.job_title,
        job_description=row.job_description,
        geography=row.geography,
        essential_skills=row.essential_skills,
        preferred_skills=row.preferred_skills,
    )
    record = _WorkflowRecord(
        expires_at=0,
        request=stored_request,
        skills=tuple(skills),
        response=current,
    )
    updated, changed = _build_review_transition(record, request)
    if not changed:
        db.rollback()
        return current

    reviewed_at = updated.human_review.attested_at or _now()
    review.decision = request.decision
    review.reviewed_requirement_ids = list(request.reviewed_requirement_ids)
    review.review_completed_at = reviewed_at
    row.workflow_status = updated.workflow_status
    row.stages_snapshot = [item.model_dump(mode="json") for item in updated.stages]
    row.learning_pathways_snapshot = updated.learning_pathways.model_dump(mode="json")
    row.workflow_summary = updated.workflow_summary
    row.version += 1
    row.updated_at = reviewed_at
    db.flush()
    # Review notes remain intentionally transient and are not echoed by the
    # persistent API, keeping repeated decisions response-idempotent.
    db.commit()
    committed = workflow_response(db, row)
    return committed
