from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database
from app.bias_audit_persistence import (
    get_bias_audit,
    list_bias_audits,
    save_bias_audit,
    update_bias_audit_review,
)
from app.db_models import Base, BiasAudit, BiasAuditReviewSelection, User
from app.models import AuditCriterion, BiasAuditRequest, BiasAuditReviewUpdateRequest


def _database():
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _user(db: Session, address: str) -> User:
    user = User(email_normalized=address, password_hash="$argon2id$test")
    db.add(user)
    db.flush()
    return user


def test_standalone_audit_and_review_selection_persist_owner_scoped_without_full_input():
    engine = _database()
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    try:
        with Session(engine) as db:
            owner_a = _user(db, "bias-a@example.test")
            owner_b = _user(db, "bias-b@example.test")
            owner_a_id, owner_b_id = owner_a.id, owner_b.id
            db.commit()
            request = BiasAuditRequest(
                job_title="Data Analyst",
                job_description="The full sensitive internal job description is intentionally not persisted.",
                requirements=[AuditCriterion(text="Minimum 8 years of continuous professional experience", importance="essential")],
            )
            saved = save_bias_audit(db, owner_a_id, request, now=now)
            audit_id = saved.audit_id
            assert saved.audit_id
            assert saved.result.finding_count == 1
            assert saved.review_selections[0].review_state == "needs_review"
            assert saved.created_at.replace(tzinfo=timezone.utc) == now
            row = db.scalar(select(BiasAudit).where(BiasAudit.public_id == audit_id))
            assert row is not None
            assert "full sensitive internal job description" not in str(row.result_snapshot)
            assert not hasattr(row, "job_description")

            list_a = list_bias_audits(db, owner_a_id)
            assert len(list_a.audits) == 1 and list_a.audits[0].finding_count == 1
            assert list_bias_audits(db, owner_b_id).audits == []
            assert get_bias_audit(db, owner_b_id, audit_id) is None
            assert update_bias_audit_review(
                db, owner_b_id, audit_id, 0, BiasAuditReviewUpdateRequest(review_state="accepted"), now=now,
            ) is None

            updated_at = datetime(2026, 11, 1, 0, 5, tzinfo=timezone.utc)
            restored = update_bias_audit_review(
                db, owner_a_id, audit_id, 0, BiasAuditReviewUpdateRequest(review_state="accepted"), now=updated_at,
            )
            assert restored is not None
            assert restored.review_selections[0].review_state == "accepted"
            assert restored.review_selections[0].updated_at.replace(tzinfo=timezone.utc) == updated_at

        # A new session simulates application request/process restoration.
        with Session(engine) as db:
            restored = get_bias_audit(db, owner_a_id, audit_id)
            assert restored is not None
            assert restored.review_selections[0].review_state == "accepted"
            assert db.scalar(select(BiasAuditReviewSelection.id).where(
                BiasAuditReviewSelection.owner_user_id == owner_b_id,
            )) is None
    finally:
        engine.dispose()


def test_saved_audit_with_no_findings_has_no_review_rows_and_unknown_index_is_not_found():
    engine = _database()
    try:
        with Session(engine) as db:
            owner = _user(db, "bias-empty@example.test")
            owner_id = owner.id
            db.commit()
            saved = save_bias_audit(db, owner_id, BiasAuditRequest(job_description="Clear role requirements."))
            assert saved.result.findings == []
            assert saved.review_selections == []
            assert update_bias_audit_review(
                db, owner_id, saved.audit_id, 0, BiasAuditReviewUpdateRequest(review_state="dismissed"),
            ) is None
    finally:
        engine.dispose()
