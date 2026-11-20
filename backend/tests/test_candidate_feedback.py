from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database
from app.db_models import Base, CandidateExperienceFeedback, User
from app.impact_persistence import (
    PERSISTED_MEASUREMENT_RETENTION_DAYS,
    get_account_impact_summary,
    submit_candidate_feedback,
)
from app.models import CandidateImpactFeedbackRequest


def _database():
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _user(db: Session, address: str) -> User:
    user = User(email_normalized=address, password_hash="$argon2id$test")
    db.add(user)
    db.flush()
    return user


def _payload(submission_id=None, **updates) -> CandidateImpactFeedbackRequest:
    values = {
        "submission_id": submission_id or uuid4(),
        "consent_to_aggregate": True,
        "application_experience": 4,
        "process_accessibility": 3,
        "job_requirements_clarity": 5,
        "perceived_fairness": 2,
        "suggestion_categories": ["clarity", "communication"],
    }
    return CandidateImpactFeedbackRequest(**{**values, **updates})


def test_candidate_feedback_is_account_associated_idempotent_and_aggregated_by_dimension():
    engine = _database()
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    try:
        with Session(engine) as db:
            owner_a = _user(db, "candidate-a@example.test")
            owner_b = _user(db, "candidate-b@example.test")
            db.commit()
            first_payload = _payload()
            first = submit_candidate_feedback(db, owner_a.id, first_payload, now=now)
            duplicate = submit_candidate_feedback(db, owner_a.id, first_payload, now=now + timedelta(seconds=1))
            assert first.duplicate is False and duplicate.duplicate is True
            assert str(first_payload.submission_id) not in first.model_dump_json()

            for _ in range(4):
                submit_candidate_feedback(db, owner_a.id, _payload(), now=now)
            submit_candidate_feedback(db, owner_b.id, _payload(), now=now)
            rows = db.scalars(select(CandidateExperienceFeedback).where(
                CandidateExperienceFeedback.owner_user_id == owner_a.id,
            )).all()
            assert len(rows) == 5
            assert all(row.owner_user_id == owner_a.id and row.submission_id for row in rows)
            assert all(row.expires_at.replace(tzinfo=timezone.utc) == now + timedelta(days=PERSISTED_MEASUREMENT_RETENTION_DAYS) for row in rows)
            assert all(row.suggestion_categories == ["clarity", "communication"] for row in rows)

            summary_a = get_account_impact_summary(db, owner_a.id, now=now + timedelta(minutes=1))
            summary_b = get_account_impact_summary(db, owner_b.id, now=now + timedelta(minutes=1))
            metrics_a = {item.metric_id: item for item in summary_a.metrics}
            metrics_b = {item.metric_id: item for item in summary_b.metrics}
            assert metrics_a["candidate_application_experience"].value == 4
            assert metrics_a["candidate_process_accessibility"].value == 3
            assert metrics_a["candidate_job_requirements_clarity"].value == 5
            assert metrics_a["candidate_perceived_fairness"].value == 2
            assert all(metrics_a[key].sample_size == 5 and metrics_a[key].data_availability == "measured" for key in (
                "candidate_application_experience", "candidate_process_accessibility",
                "candidate_job_requirements_clarity", "candidate_perceived_fairness",
            ))
            assert metrics_b["candidate_application_experience"].sample_size == 1
            assert metrics_b["candidate_application_experience"].value is None
            distributions = {item.metric_id: item for item in summary_a.candidate_feedback_distributions}
            assert distributions["candidate_application_experience"].distribution[3].value == 5
            assert summary_a.candidate_suggestion_distribution[0].value == 5
            assert not summary_a.candidate_rating_distribution
            serialized = summary_a.model_dump_json()
            assert str(owner_a.id) not in serialized and str(first_payload.submission_id) not in serialized
            assert "candidate_experience_average" not in metrics_a
    finally:
        engine.dispose()


def test_expired_candidate_feedback_cleanup_is_owner_scoped():
    engine = _database()
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    try:
        with Session(engine) as db:
            owner_a = _user(db, "candidate-expired-a@example.test")
            owner_b = _user(db, "candidate-expired-b@example.test")
            db.add_all([
                CandidateExperienceFeedback(
                    owner_user_id=owner_a.id, submission_id=uuid4(), consent_to_aggregate=True,
                    application_experience=1, process_accessibility=1, job_requirements_clarity=1,
                    perceived_fairness=1, suggestion_categories=[], submitted_at=now - timedelta(days=91),
                    expires_at=now - timedelta(days=1),
                ),
                CandidateExperienceFeedback(
                    owner_user_id=owner_b.id, submission_id=uuid4(), consent_to_aggregate=True,
                    application_experience=5, process_accessibility=5, job_requirements_clarity=5,
                    perceived_fairness=5, suggestion_categories=[], submitted_at=now,
                    expires_at=now + timedelta(days=1),
                ),
            ])
            db.commit()
            get_account_impact_summary(db, owner_a.id, now=now)
            assert db.scalar(select(CandidateExperienceFeedback.id).where(CandidateExperienceFeedback.owner_user_id == owner_a.id)) is None
            assert db.scalar(select(CandidateExperienceFeedback.id).where(CandidateExperienceFeedback.owner_user_id == owner_b.id)) is not None
    finally:
        engine.dispose()
