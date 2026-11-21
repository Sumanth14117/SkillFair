"""Owner-scoped account data inventory, export and deletion operations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .db_models import (
    AnalysisSkill,
    BiasAudit,
    BiasAuditReviewSelection,
    CandidateExperienceFeedback,
    CandidateFeedback,
    CareerWorkflow,
    ImpactObservation,
    RecruiterFeedback,
    ResumeAnalysis,
    User,
    WorkflowReview,
)
from .models import (
    AccountAnalysisItem,
    AccountDataResponse,
    AccountExportAnalysis,
    AccountExportCandidateFeedback,
    AccountExportCandidateExperienceFeedback,
    AccountExportBiasAudit,
    AccountExportBiasAuditReview,
    AccountExportImpactObservation,
    AccountExportProfile,
    AccountExportRecruiterFeedback,
    AccountExportResponse,
    AccountExportReview,
    AccountExportSkill,
    AccountExportWorkflow,
    AccountWorkflowItem,
    SkillEvidence,
)
from .privacy import redact_skill_evidence

MAX_EXPORT_ROWS_PER_TABLE = 250
MAX_EXPORT_BYTES = 2 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    value = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
    return value <= now


def _count(db: Session, model: type, owner_user_id: UUID) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(model.owner_user_id == owner_user_id)) or 0)


def get_account_data(db: Session, owner_user_id: UUID, limit: int = 25, offset: int = 0) -> AccountDataResponse:
    """Return paged metadata only; evidence excerpts and request bodies are omitted."""
    now = _now()
    analyses = db.scalars(
        select(ResumeAnalysis)
        .where(ResumeAnalysis.owner_user_id == owner_user_id)
        .order_by(ResumeAnalysis.created_at.desc(), ResumeAnalysis.id.desc())
        .offset(offset).limit(limit)
    ).all()
    workflows = db.scalars(
        select(CareerWorkflow)
        .where(CareerWorkflow.owner_user_id == owner_user_id)
        .order_by(CareerWorkflow.created_at.desc(), CareerWorkflow.id.desc())
        .offset(offset).limit(limit)
    ).all()
    workflow_ids = [row.id for row in workflows]
    reviews = db.scalars(select(WorkflowReview).where(
        WorkflowReview.owner_user_id == owner_user_id,
        WorkflowReview.workflow_id.in_(workflow_ids),
    )).all() if workflow_ids else []
    review_by_workflow = {item.workflow_id: item for item in reviews}

    analysis_items = []
    for row in analyses:
        skill_count = int(db.scalar(select(func.count()).select_from(AnalysisSkill).where(
            AnalysisSkill.analysis_id == row.id,
            AnalysisSkill.owner_user_id == owner_user_id,
        )) or 0)
        linked_workflow_count = int(db.scalar(select(func.count()).select_from(CareerWorkflow).where(
            CareerWorkflow.analysis_id == row.id,
            CareerWorkflow.owner_user_id == owner_user_id,
        )) or 0)
        analysis_items.append(AccountAnalysisItem(
            analysis_id=row.public_id,
            created_at=row.created_at,
            analysis_mode=row.analysis_mode,
            skill_count=skill_count,
            linked_workflow_count=linked_workflow_count,
            status="deleted" if row.deleted_at is not None else "expired" if _expired(row.expires_at, now) else "available",
        ))

    workflow_items = [AccountWorkflowItem(
        run_id=row.public_id,
        job_title=row.job_title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        workflow_status=row.workflow_status,
        review_decision=review_by_workflow[row.id].decision if row.id in review_by_workflow else None,
        status="expired" if _expired(row.expires_at, now) else "available",
    ) for row in workflows]

    return AccountDataResponse(
        analysis_total=_count(db, ResumeAnalysis, owner_user_id),
        workflow_total=_count(db, CareerWorkflow, owner_user_id),
        limit=limit,
        offset=offset,
        analyses=analysis_items,
        workflows=workflow_items,
    )


def _bounded_rows(db: Session, statement):
    rows = db.scalars(statement.limit(MAX_EXPORT_ROWS_PER_TABLE + 1)).all()
    if len(rows) > MAX_EXPORT_ROWS_PER_TABLE:
        raise HTTPException(status_code=413, detail="This account export exceeds the supported record limit. Contact the administrator for assistance.")
    return rows


def build_account_export(db: Session, owner_user_id: UUID) -> AccountExportResponse:
    """Build a bounded export of this account's persisted records only."""
    user = db.scalar(select(User).where(User.id == owner_user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="Account data was not found.")

    analyses = _bounded_rows(db, select(ResumeAnalysis).where(
        ResumeAnalysis.owner_user_id == owner_user_id,
    ).order_by(ResumeAnalysis.created_at, ResumeAnalysis.id))
    workflows = _bounded_rows(db, select(CareerWorkflow).where(
        CareerWorkflow.owner_user_id == owner_user_id,
    ).order_by(CareerWorkflow.created_at, CareerWorkflow.id))
    analysis_ids = [row.id for row in analyses]
    workflow_ids = [row.id for row in workflows]
    skills = _bounded_rows(db, select(AnalysisSkill).where(
        AnalysisSkill.owner_user_id == owner_user_id,
        AnalysisSkill.analysis_id.in_(analysis_ids),
    ).order_by(AnalysisSkill.created_at, AnalysisSkill.id)) if analysis_ids else []
    reviews = _bounded_rows(db, select(WorkflowReview).where(
        WorkflowReview.owner_user_id == owner_user_id,
        WorkflowReview.workflow_id.in_(workflow_ids),
    ).order_by(WorkflowReview.created_at, WorkflowReview.id)) if workflow_ids else []
    recruiter_feedback = _bounded_rows(db, select(RecruiterFeedback).where(
        RecruiterFeedback.owner_user_id == owner_user_id,
    ).order_by(RecruiterFeedback.submitted_at, RecruiterFeedback.id))
    candidate_feedback = _bounded_rows(db, select(CandidateFeedback).where(
        CandidateFeedback.owner_user_id == owner_user_id,
    ).order_by(CandidateFeedback.submitted_at, CandidateFeedback.id))
    candidate_experience_feedback = _bounded_rows(db, select(CandidateExperienceFeedback).where(
        CandidateExperienceFeedback.owner_user_id == owner_user_id,
    ).order_by(CandidateExperienceFeedback.submitted_at, CandidateExperienceFeedback.id))
    bias_audits = _bounded_rows(db, select(BiasAudit).where(
        BiasAudit.owner_user_id == owner_user_id,
    ).order_by(BiasAudit.created_at, BiasAudit.id))
    bias_audit_ids = [item.id for item in bias_audits]
    bias_audit_reviews = _bounded_rows(db, select(BiasAuditReviewSelection).where(
        BiasAuditReviewSelection.owner_user_id == owner_user_id,
        BiasAuditReviewSelection.audit_id.in_(bias_audit_ids),
    ).order_by(BiasAuditReviewSelection.created_at, BiasAuditReviewSelection.id)) if bias_audit_ids else []
    observations = _bounded_rows(db, select(ImpactObservation).where(
        ImpactObservation.owner_user_id == owner_user_id,
    ).order_by(ImpactObservation.created_at, ImpactObservation.id))

    row_count = sum(map(len, (analyses, workflows, skills, reviews, recruiter_feedback, candidate_feedback, candidate_experience_feedback, bias_audits, bias_audit_reviews, observations)))
    if row_count > 1000:
        raise HTTPException(status_code=413, detail="This account export exceeds the supported record limit. Contact the administrator for assistance.")

    analysis_by_id = {row.id: row for row in analyses}
    workflow_by_id = {row.id: row for row in workflows}
    review_by_workflow = {item.workflow_id: item for item in reviews}
    review_by_id = {item.id: item for item in reviews}
    skills_by_analysis: dict[UUID, list[AccountExportSkill]] = {}
    for item in skills:
        try:
            safe_skill = redact_skill_evidence(SkillEvidence(
                skill_name=item.skill_name,
                evidence_type=item.evidence_type,
                detection_method=item.detection_method,
                validation_status=item.validation_status,
                supporting_excerpt=item.supporting_excerpt,
                relevance_explanation=item.relevance_explanation,
            ))
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail="A saved analysis contains a field that cannot be exported safely. Delete that analysis or contact support.",
            ) from exc
        skills_by_analysis.setdefault(item.analysis_id, []).append(AccountExportSkill(
            skill_name=safe_skill.skill_name,
            evidence_type=safe_skill.evidence_type,
            detection_method=safe_skill.detection_method,
            validation_status=safe_skill.validation_status,
            supporting_excerpt=safe_skill.supporting_excerpt,
            relevance_explanation=safe_skill.relevance_explanation,
        ))

    export = AccountExportResponse(
        exported_at=_now(),
        account=AccountExportProfile(email=user.email_normalized, created_at=user.created_at),
        analyses=[AccountExportAnalysis(
            analysis_id=row.public_id,
            analysis_mode=row.analysis_mode,
            created_at=row.created_at,
            expires_at=row.expires_at,
            deleted_at=row.deleted_at,
            skills=skills_by_analysis.get(row.id, []),
        ) for row in analyses],
        workflows=[AccountExportWorkflow(
            run_id=row.public_id,
            job_title=row.job_title,
            job_description=row.job_description,
            geography=row.geography,
            essential_skills=list(row.essential_skills),
            preferred_skills=list(row.preferred_skills),
            workflow_status=row.workflow_status,
            workflow_summary=row.workflow_summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            review=(AccountExportReview(
                decision=review_by_workflow[row.id].decision,
                reviewed_requirement_ids=list(review_by_workflow[row.id].reviewed_requirement_ids or []),
                review_started_at=review_by_workflow[row.id].review_started_at,
                review_completed_at=review_by_workflow[row.id].review_completed_at,
            ) if row.id in review_by_workflow else None),
        ) for row in workflows],
        recruiter_feedback=[AccountExportRecruiterFeedback(
            workflow_id=workflow_by_id[item.workflow_id].public_id if item.workflow_id in workflow_by_id else None,
            consent_to_aggregate=item.consent_to_aggregate,
            evidence_usefulness=item.evidence_usefulness,
            explanation_clarity=item.explanation_clarity,
            review_ease=item.review_ease,
            submitted_at=item.submitted_at,
            expires_at=item.expires_at,
        ) for item in recruiter_feedback],
        candidate_feedback=[AccountExportCandidateFeedback(
            analysis_id=analysis_by_id[item.analysis_id].public_id if item.analysis_id in analysis_by_id else None,
            consent_to_aggregate=item.consent_to_aggregate,
            skill_representation_clarity=item.skill_representation_clarity,
            understanding_of_skills=item.understanding_of_skills,
            perceived_transparency=item.perceived_transparency,
            overall_experience=item.overall_experience,
            submitted_at=item.submitted_at,
            expires_at=item.expires_at,
        ) for item in candidate_feedback],
        candidate_experience_feedback=[AccountExportCandidateExperienceFeedback(
            consent_to_aggregate=item.consent_to_aggregate,
            application_experience=item.application_experience,
            process_accessibility=item.process_accessibility,
            job_requirements_clarity=item.job_requirements_clarity,
            perceived_fairness=item.perceived_fairness,
            suggestion_categories=list(item.suggestion_categories or []),
            submitted_at=item.submitted_at,
            expires_at=item.expires_at,
        ) for item in candidate_experience_feedback],
        bias_audits=[AccountExportBiasAudit(
            audit_id=item.public_id,
            job_title=item.job_title,
            result=item.result_snapshot,
            review_selections=[AccountExportBiasAuditReview(
                finding_index=review.finding_index,
                review_state=review.review_state,
                created_at=review.created_at,
                updated_at=review.updated_at,
            ) for review in bias_audit_reviews if review.audit_id == item.id],
            created_at=item.created_at,
            updated_at=item.updated_at,
        ) for item in bias_audits],
        impact_observations=[AccountExportImpactObservation(
            observation_type=item.observation_type,
            numeric_value=float(item.numeric_value),
            created_at=item.created_at,
            expires_at=item.expires_at,
            workflow_id=(
                workflow_by_id[review_by_id[item.workflow_review_id].workflow_id].public_id
                if item.workflow_review_id in review_by_id and review_by_id[item.workflow_review_id].workflow_id in workflow_by_id else None
            ),
        ) for item in observations],
    )

    encoded = export.model_dump_json().encode("utf-8")
    if len(encoded) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="This account export exceeds the supported size limit. Contact the administrator for assistance.")
    return export


def delete_analysis(db: Session, owner_user_id: UUID, public_id: str) -> None:
    """Hard-delete an owned analysis only after its dependent workflows are removed."""
    row = db.scalar(select(ResumeAnalysis).where(
        ResumeAnalysis.public_id == public_id,
        ResumeAnalysis.owner_user_id == owner_user_id,
    ).with_for_update())
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis was not found.")
    dependent_workflow = db.scalar(select(CareerWorkflow.id).where(
        CareerWorkflow.analysis_id == row.id,
        CareerWorkflow.owner_user_id == owner_user_id,
    ).limit(1))
    if dependent_workflow is not None:
        raise HTTPException(status_code=409, detail="Delete the workflows linked to this analysis before deleting the analysis.")
    db.delete(row)
    db.commit()


def delete_workflow(db: Session, owner_user_id: UUID, public_id: str) -> None:
    """Atomically delete one owned workflow; dependent rows use database cascades."""
    result = db.execute(delete(CareerWorkflow).where(
        CareerWorkflow.public_id == public_id,
        CareerWorkflow.owner_user_id == owner_user_id,
    ))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail="Workflow was not found.")
    db.commit()
