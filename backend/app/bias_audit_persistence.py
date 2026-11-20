"""Owner-scoped persistence for standalone deterministic Bias Audit results."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .bias_audit import audit_job
from .db_models import BiasAudit, BiasAuditReviewSelection
from .models import (
    BiasAuditRequest,
    BiasAuditResponse,
    BiasAuditReviewSelectionResponse,
    BiasAuditReviewUpdateRequest,
    SavedBiasAuditDetail,
    SavedBiasAuditListResponse,
    SavedBiasAuditSummary,
)


def _summary(row: BiasAudit, reviews: list[BiasAuditReviewSelection]) -> SavedBiasAuditSummary:
    result = BiasAuditResponse.model_validate(row.result_snapshot)
    return SavedBiasAuditSummary(
        audit_id=row.public_id,
        job_title=row.job_title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        criteria_reviewed=result.criteria_reviewed,
        finding_count=result.finding_count,
        reviewed_finding_count=sum(item.review_state != "needs_review" for item in reviews),
    )


def _detail(row: BiasAudit, reviews: list[BiasAuditReviewSelection]) -> SavedBiasAuditDetail:
    result = BiasAuditResponse.model_validate(row.result_snapshot)
    return SavedBiasAuditDetail(
        **_summary(row, reviews).model_dump(),
        result=result,
        review_selections=[BiasAuditReviewSelectionResponse(
            finding_index=item.finding_index,
            review_state=item.review_state,
            updated_at=item.updated_at,
        ) for item in reviews],
    )


def save_bias_audit(db: Session, owner_id: UUID, request: BiasAuditRequest, *, now: datetime | None = None) -> SavedBiasAuditDetail:
    """Recompute server-side and persist only the result, never the full job input."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = audit_job(request)
    row = BiasAudit(
        public_id=uuid4().hex,
        owner_user_id=owner_id,
        job_title=result.job_title,
        result_snapshot=result.model_dump(mode="json"),
        created_at=current,
        updated_at=current,
    )
    db.add(row)
    db.flush()
    reviews = [BiasAuditReviewSelection(
        audit_id=row.id,
        owner_user_id=owner_id,
        finding_index=index,
        review_state="needs_review",
        created_at=current,
        updated_at=current,
    ) for index, _finding in enumerate(result.findings)]
    db.add_all(reviews)
    db.commit()
    db.refresh(row)
    return _detail(row, reviews)


def list_bias_audits(db: Session, owner_id: UUID, *, limit: int = 20) -> SavedBiasAuditListResponse:
    rows = db.scalars(select(BiasAudit).where(
        BiasAudit.owner_user_id == owner_id,
    ).order_by(BiasAudit.created_at.desc(), BiasAudit.id.desc()).limit(limit)).all()
    if not rows:
        return SavedBiasAuditListResponse(audits=[])
    ids = [row.id for row in rows]
    selections = db.scalars(select(BiasAuditReviewSelection).where(
        BiasAuditReviewSelection.owner_user_id == owner_id,
        BiasAuditReviewSelection.audit_id.in_(ids),
    )).all()
    by_audit: dict[UUID, list[BiasAuditReviewSelection]] = {item.id: [] for item in rows}
    for item in selections:
        by_audit[item.audit_id].append(item)
    return SavedBiasAuditListResponse(audits=[_summary(row, by_audit[row.id]) for row in rows])


def get_bias_audit(db: Session, owner_id: UUID, public_id: str) -> SavedBiasAuditDetail | None:
    row = db.scalar(select(BiasAudit).where(
        BiasAudit.public_id == public_id,
        BiasAudit.owner_user_id == owner_id,
    ))
    if row is None:
        return None
    reviews = db.scalars(select(BiasAuditReviewSelection).where(
        BiasAuditReviewSelection.audit_id == row.id,
        BiasAuditReviewSelection.owner_user_id == owner_id,
    ).order_by(BiasAuditReviewSelection.finding_index)).all()
    return _detail(row, reviews)


def update_bias_audit_review(
    db: Session,
    owner_id: UUID,
    public_id: str,
    finding_index: int,
    request: BiasAuditReviewUpdateRequest,
    *,
    now: datetime | None = None,
) -> SavedBiasAuditDetail | None:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    row = db.scalar(select(BiasAudit).where(
        BiasAudit.public_id == public_id,
        BiasAudit.owner_user_id == owner_id,
    ).with_for_update())
    if row is None:
        return None
    result = BiasAuditResponse.model_validate(row.result_snapshot)
    if finding_index < 0 or finding_index >= len(result.findings):
        return None
    selection = db.scalar(select(BiasAuditReviewSelection).where(
        BiasAuditReviewSelection.audit_id == row.id,
        BiasAuditReviewSelection.owner_user_id == owner_id,
        BiasAuditReviewSelection.finding_index == finding_index,
    ).with_for_update())
    if selection is None:
        return None
    selection.review_state = request.review_state
    selection.updated_at = current
    row.updated_at = current
    db.commit()
    db.refresh(row)
    reviews = db.scalars(select(BiasAuditReviewSelection).where(
        BiasAuditReviewSelection.audit_id == row.id,
        BiasAuditReviewSelection.owner_user_id == owner_id,
    ).order_by(BiasAuditReviewSelection.finding_index)).all()
    return _detail(row, reviews)


def delete_bias_audit(db: Session, owner_id: UUID, public_id: str) -> bool:
    row = db.scalar(select(BiasAudit).where(
        BiasAudit.public_id == public_id,
        BiasAudit.owner_user_id == owner_id,
    ).with_for_update())
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
