"""Owner-scoped search and persisted notifications for actual workflow events."""

from __future__ import annotations

from datetime import datetime, timezone
from secrets import token_hex
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .db_models import AnalysisSkill, BiasAudit, CareerWorkflow, ResumeAnalysis, UserNotification
from .models import SearchResponse, SearchResult, UserNotificationListResponse, UserNotificationResponse


MAX_NOTIFICATION_ROWS_PER_ACCOUNT = 100


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_account_records(db: Session, owner_id: UUID, query: str, limit: int = 20) -> SearchResponse:
    normalized = query.strip()
    if not 2 <= len(normalized) <= 80:
        raise ValueError("Search must contain between 2 and 80 characters.")
    pattern = f"%{_escape_like(normalized)}%"
    results: list[SearchResult] = []

    # Search skill labels only. Evidence excerpts, resume text and contact details
    # are intentionally excluded from search and its response.
    skill_rows = db.execute(
        select(ResumeAnalysis.public_id, ResumeAnalysis.created_at, AnalysisSkill.skill_name)
        .join(AnalysisSkill, (AnalysisSkill.analysis_id == ResumeAnalysis.id) & (AnalysisSkill.owner_user_id == ResumeAnalysis.owner_user_id))
        .where(
            ResumeAnalysis.owner_user_id == owner_id,
            AnalysisSkill.owner_user_id == owner_id,
            ResumeAnalysis.deleted_at.is_(None),
            (ResumeAnalysis.expires_at.is_(None)) | (ResumeAnalysis.expires_at > datetime.now(timezone.utc)),
            AnalysisSkill.skill_name.ilike(pattern, escape="\\"),
        )
        .order_by(ResumeAnalysis.created_at.desc(), ResumeAnalysis.id.desc())
        .limit(limit * 4)
    ).all()
    skills_by_analysis: dict[str, tuple[datetime, list[str]]] = {}
    for public_id, created_at, skill_name in skill_rows:
        key = str(public_id)
        if key not in skills_by_analysis:
            skills_by_analysis[key] = (created_at, [])
        if skill_name not in skills_by_analysis[key][1]:
            skills_by_analysis[key][1].append(skill_name)
    for public_id, (created_at, names) in skills_by_analysis.items():
        results.append(SearchResult(
            record_type="analysis", record_id=public_id, title="Skill Passport",
            detail="Matching skill labels: " + ", ".join(names[:8]), page="Skills Discovery", updated_at=created_at,
        ))

    workflow_rows = db.scalars(select(CareerWorkflow).where(
        CareerWorkflow.owner_user_id == owner_id,
        (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > datetime.now(timezone.utc)),
        CareerWorkflow.job_title.ilike(pattern, escape="\\"),
    ).order_by(CareerWorkflow.updated_at.desc(), CareerWorkflow.id.desc()).limit(limit)).all()
    results.extend(SearchResult(
        record_type="workflow", record_id=row.public_id, title=row.job_title,
        detail=f"Career workflow · {row.workflow_status.replace('_', ' ')}",
        page="Career Orchestrator", updated_at=row.updated_at,
    ) for row in workflow_rows)

    audit_rows = db.scalars(select(BiasAudit).where(
        BiasAudit.owner_user_id == owner_id,
        BiasAudit.job_title.ilike(pattern, escape="\\"),
    ).order_by(BiasAudit.updated_at.desc(), BiasAudit.id.desc()).limit(limit)).all()
    results.extend(SearchResult(
        record_type="bias_audit", record_id=row.public_id, title=row.job_title or "Saved Bias Audit",
        detail="Saved deterministic wording review", page="Bias Audit", updated_at=row.updated_at,
    ) for row in audit_rows)

    results.sort(key=lambda item: item.updated_at, reverse=True)
    return SearchResponse(query=normalized, results=results[:limit])


def add_workflow_notifications(
    db: Session,
    *,
    owner_id: UUID,
    workflow: CareerWorkflow,
    decision: str,
    created_at: datetime,
    pathways_ready: bool,
) -> None:
    """Add notifications in the workflow transaction; unique event keys prevent duplicates."""
    decision_label = decision.capitalize()
    db.add(UserNotification(
        public_id=token_hex(16), owner_user_id=owner_id, workflow_id=workflow.id,
        source_event_key=f"workflow:{workflow.id}:decision",
        notification_type="workflow_decision",
        title="Human Review recorded",
        message=f"Your review decision was recorded: {decision_label} · {workflow.job_title}.",
        created_at=created_at,
    ))
    if pathways_ready:
        db.add(UserNotification(
            public_id=token_hex(16), owner_user_id=owner_id, workflow_id=workflow.id,
            source_event_key=f"workflow:{workflow.id}:learning-pathways",
            notification_type="learning_pathways_ready",
            title="Learning Pathways are ready",
            message=f"The completed Learning Pathways are available for {workflow.job_title}.",
            created_at=created_at,
        ))
    db.flush()
    retained_ids = select(UserNotification.id).where(
        UserNotification.owner_user_id == owner_id,
    ).order_by(UserNotification.created_at.desc(), UserNotification.id.desc()).limit(MAX_NOTIFICATION_ROWS_PER_ACCOUNT)
    db.execute(delete(UserNotification).where(
        UserNotification.owner_user_id == owner_id,
        UserNotification.id.not_in(retained_ids),
    ))


def list_notifications(db: Session, owner_id: UUID, limit: int = 20) -> UserNotificationListResponse:
    rows = db.scalars(select(UserNotification).where(
        UserNotification.owner_user_id == owner_id,
    ).order_by(UserNotification.created_at.desc(), UserNotification.id.desc()).limit(limit)).all()
    unread_count = db.scalar(select(func.count()).select_from(UserNotification).where(
        UserNotification.owner_user_id == owner_id,
        UserNotification.read_at.is_(None),
    )) or 0
    workflows = {}
    if rows:
        workflows = dict(db.execute(select(CareerWorkflow.id, CareerWorkflow.public_id).where(
            CareerWorkflow.owner_user_id == owner_id,
            CareerWorkflow.id.in_([row.workflow_id for row in rows if row.workflow_id is not None]),
        )).all())
    return UserNotificationListResponse(
        notifications=[UserNotificationResponse(
            notification_id=row.public_id,
            notification_type=row.notification_type,
            title=row.title,
            message=row.message,
            created_at=row.created_at,
            read_at=row.read_at,
            workflow_id=workflows.get(row.workflow_id),
        ) for row in rows],
        unread_count=int(unread_count),
    )


def mark_notification_read(db: Session, owner_id: UUID, public_id: str, *, now: datetime | None = None) -> UserNotification | None:
    row = db.scalar(select(UserNotification).where(
        UserNotification.public_id == public_id,
        UserNotification.owner_user_id == owner_id,
    ).with_for_update())
    if row is None:
        return None
    if row.read_at is None:
        row.read_at = now or datetime.now(timezone.utc)
        db.commit()
    return row
