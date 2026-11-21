"""Owner-scoped database queries for the Overview dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .db_models import CareerWorkflow, ResumeAnalysis, WorkflowReview
from .models import (
    DashboardOverviewResponse,
    DashboardRecentActivity,
    DashboardRecentWorkflow,
    DashboardReviewDecisionCount,
    DashboardSummary,
    DashboardWorkflowStatusCount,
)

RECENT_WORKFLOW_LIMIT = 8
RECENT_ACTIVITY_LIMIT = 12


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _active_workflow_filters(owner_user_id: UUID, now: datetime):
    return (
        CareerWorkflow.owner_user_id == owner_user_id,
        (CareerWorkflow.expires_at.is_(None)) | (CareerWorkflow.expires_at > now),
    )


def _review_join():
    return and_(
        WorkflowReview.workflow_id == CareerWorkflow.id,
        WorkflowReview.owner_user_id == CareerWorkflow.owner_user_id,
    )


def get_dashboard_overview(
    db: Session,
    owner_user_id: UUID,
    *,
    generated_at: datetime | None = None,
) -> DashboardOverviewResponse:
    """Return metrics and recent persisted activity for one authenticated owner."""
    now = _utc(generated_at or datetime.now(timezone.utc))
    active_workflow_filters = _active_workflow_filters(owner_user_id, now)

    workflow_groups = db.execute(
        select(CareerWorkflow.workflow_status, WorkflowReview.decision, func.count())
        .outerjoin(WorkflowReview, _review_join())
        .where(*active_workflow_filters)
        .group_by(CareerWorkflow.workflow_status, WorkflowReview.decision)
    ).all()

    status_counts: dict[str, int] = {}
    decision_counts: dict[str, int] = {}
    pending_reviews = 0
    completed_reviews = 0
    total_workflows = 0
    for status, decision, count in workflow_groups:
        total_workflows += count
        status_counts[status] = status_counts.get(status, 0) + count
        normalized_decision = decision or "pending"
        decision_counts[normalized_decision] = decision_counts.get(normalized_decision, 0) + count
        if decision is None:
            pending_reviews += count
        else:
            completed_reviews += count

    saved_analyses = db.scalar(
        select(func.count()).select_from(ResumeAnalysis).where(
            ResumeAnalysis.owner_user_id == owner_user_id,
            ResumeAnalysis.deleted_at.is_(None),
            (ResumeAnalysis.expires_at.is_(None)) | (ResumeAnalysis.expires_at > now),
        )
    ) or 0

    recent_workflow_rows = db.execute(
        select(
            CareerWorkflow.public_id,
            CareerWorkflow.job_title,
            CareerWorkflow.workflow_status,
            WorkflowReview.decision,
            CareerWorkflow.created_at,
            CareerWorkflow.updated_at,
        )
        .outerjoin(WorkflowReview, _review_join())
        .where(*active_workflow_filters)
        .order_by(CareerWorkflow.created_at.desc(), CareerWorkflow.id.desc())
        .limit(RECENT_WORKFLOW_LIMIT)
    ).all()

    recent_workflows = [
        DashboardRecentWorkflow(
            run_id=run_id,
            job_title=job_title,
            workflow_status=status,
            review_decision=decision or "pending",
            created_at=_utc(created_at),
            updated_at=_utc(updated_at),
        )
        for run_id, job_title, status, decision, created_at, updated_at in recent_workflow_rows
    ]

    events: list[DashboardRecentActivity] = []
    workflow_created_rows = db.execute(
        select(
            CareerWorkflow.public_id,
            CareerWorkflow.job_title,
            CareerWorkflow.created_at,
            CareerWorkflow.workflow_status,
        )
        .where(*active_workflow_filters)
        .order_by(CareerWorkflow.created_at.desc(), CareerWorkflow.id.desc())
        .limit(RECENT_ACTIVITY_LIMIT)
    ).all()
    for run_id, job_title, occurred_at, status in workflow_created_rows:
        events.append(DashboardRecentActivity(
            event_type="workflow_created",
            workflow_id=run_id,
            job_title=job_title,
            occurred_at=_utc(occurred_at),
            workflow_status=status,
            description=f"Workflow created for {job_title}.",
        ))

    review_start_rows = db.execute(
        select(
            CareerWorkflow.public_id,
            CareerWorkflow.job_title,
            WorkflowReview.review_started_at,
            CareerWorkflow.workflow_status,
            WorkflowReview.decision,
        )
        .join(WorkflowReview, _review_join())
        .where(*active_workflow_filters, WorkflowReview.review_started_at.is_not(None))
        .order_by(WorkflowReview.review_started_at.desc(), WorkflowReview.id.desc())
        .limit(RECENT_ACTIVITY_LIMIT)
    ).all()
    for run_id, job_title, occurred_at, status, decision in review_start_rows:
        events.append(DashboardRecentActivity(
            event_type="review_started",
            workflow_id=run_id,
            job_title=job_title,
            occurred_at=_utc(occurred_at),
            workflow_status=status,
            decision=decision,
            description=f"Human review started for {job_title}.",
        ))

    review_completed_rows = db.execute(
        select(
            CareerWorkflow.public_id,
            CareerWorkflow.job_title,
            WorkflowReview.review_completed_at,
            CareerWorkflow.workflow_status,
            WorkflowReview.decision,
        )
        .join(WorkflowReview, _review_join())
        .where(*active_workflow_filters, WorkflowReview.review_completed_at.is_not(None))
        .order_by(WorkflowReview.review_completed_at.desc(), WorkflowReview.id.desc())
        .limit(RECENT_ACTIVITY_LIMIT)
    ).all()
    for run_id, job_title, occurred_at, status, decision in review_completed_rows:
        events.append(DashboardRecentActivity(
            event_type="review_decided",
            workflow_id=run_id,
            job_title=job_title,
            occurred_at=_utc(occurred_at),
            workflow_status=status,
            decision=decision,
            description=f"Human review recorded {decision} for {job_title}.",
        ))

    events.sort(key=lambda event: (event.occurred_at, event.workflow_id, event.event_type), reverse=True)

    return DashboardOverviewResponse(
        generated_at=now,
        summary=DashboardSummary(
            saved_analyses=saved_analyses,
            total_workflows=total_workflows,
            pending_reviews=pending_reviews,
            completed_reviews=completed_reviews,
        ),
        workflow_status_distribution=[
            DashboardWorkflowStatusCount(status=status, count=count)
            for status, count in sorted(status_counts.items())
        ],
        review_decision_distribution=[
            DashboardReviewDecisionCount(decision=decision, count=count)
            for decision, count in sorted(decision_counts.items())
        ],
        recent_workflows=recent_workflows,
        recent_activity=events[:RECENT_ACTIVITY_LIMIT],
    )
