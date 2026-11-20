from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app import database, main
from app.db_models import (
    AnalysisSkill,
    Base,
    CandidateFeedback,
    CandidateExperienceFeedback,
    BiasAudit,
    BiasAuditReviewSelection,
    CareerWorkflow,
    ImpactObservation,
    RecruiterFeedback,
    ResumeAnalysis,
    User,
    UserSession,
    UserNotification,
    WorkflowReview,
)


EXPECTED_TABLES = {
    "users", "user_sessions", "resume_analyses", "analysis_skills",
    "career_workflows", "workflow_reviews", "impact_observations",
    "recruiter_feedback", "candidate_feedback", "candidate_experience_feedback",
    "bias_audits", "bias_audit_review_selections", "user_notifications",
}


@pytest.fixture
def sqlite_engine() -> Engine:
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_orm_metadata_contains_schema_and_owner_scoped_foreign_keys():
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert User.__table__.c.password_hash.type.length >= 256
    assert "resume_text" not in ResumeAnalysis.__table__.c
    assert "file_bytes" not in ResumeAnalysis.__table__.c
    assert "token" not in UserSession.__table__.c
    assert "review_notes" not in WorkflowReview.__table__.c
    assert not any("comment" in column.name for table in Base.metadata.tables.values() for column in table.columns)
    assert "review_started_at" in WorkflowReview.__table__.c
    assert "review_completed_at" in WorkflowReview.__table__.c
    assert not any(any(marker in column.name.lower() for marker in ("resume_text", "file_bytes", "raw_token", "comment", "review_notes")) for table in Base.metadata.tables.values() for column in table.columns)

    owner_constraints = [constraint for constraint in AnalysisSkill.__table__.foreign_key_constraints if len(constraint.elements) == 2]
    assert any({element.parent.name for element in item.elements} == {"analysis_id", "owner_user_id"} for item in owner_constraints)
    workflow_owner_constraints = [constraint for constraint in WorkflowReview.__table__.foreign_key_constraints if len(constraint.elements) == 2]
    assert any({element.parent.name for element in item.elements} == {"workflow_id", "owner_user_id"} for item in workflow_owner_constraints)
    assert CareerWorkflow.__table__.c.stages_snapshot.nullable is False
    assert "response_snapshot" not in CareerWorkflow.__table__.c
    assert "review_notes" not in CareerWorkflow.__table__.c
    assert ImpactObservation.__table__.c.numeric_value.nullable is False
    assert RecruiterFeedback.__table__.c.evidence_usefulness.nullable is False
    assert CandidateFeedback.__table__.c.overall_experience.nullable is False
    assert CandidateExperienceFeedback.__table__.c.application_experience.nullable is False
    assert CandidateExperienceFeedback.__table__.c.suggestion_categories.nullable is False
    assert BiasAudit.__table__.c.result_snapshot.nullable is False
    assert "job_description" not in BiasAudit.__table__.c
    review_owner_constraints = [
        constraint for constraint in BiasAuditReviewSelection.__table__.foreign_key_constraints
        if len(constraint.elements) == 2
    ]
    assert any(
        {element.parent.name for element in constraint.elements} == {"audit_id", "owner_user_id"}
        for constraint in review_owner_constraints
    )
    notification_owner_constraints = [
        constraint for constraint in UserNotification.__table__.foreign_key_constraints
        if len(constraint.elements) == 2
    ]
    assert any(
        {element.parent.name for element in constraint.elements} == {"workflow_id", "owner_user_id"}
        for constraint in notification_owner_constraints
    )
    assert any(
        {column.name for column in constraint.columns} == {"owner_user_id", "source_event_key"}
        for constraint in UserNotification.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    )


def test_candidate_experience_suggestion_categories_server_default_matches_migration():
    # Migration 202611190001 defines the database default as the JSON literal [].
    column = CandidateExperienceFeedback.__table__.c.suggestion_categories
    assert column.server_default is not None
    assert str(column.server_default.arg) == "'[]'"


def test_sqlite_metadata_enforces_analysis_skill_ownership(sqlite_engine: Engine):
    owner_a, owner_b = uuid4(), uuid4()
    with Session(sqlite_engine) as session:
        session.add_all([
            User(id=owner_a, email_normalized="a@example.test", password_hash="$argon2id$example"),
            User(id=owner_b, email_normalized="b@example.test", password_hash="$argon2id$example"),
        ])
        session.flush()
        analysis = ResumeAnalysis(public_id="opaque-analysis-locator", owner_user_id=owner_a, analysis_mode="local")
        session.add(analysis)
        session.flush()
        session.add(AnalysisSkill(
            analysis_id=analysis.id,
            owner_user_id=owner_b,
            skill_name="Python",
            evidence_type="explicit",
            detection_method="local",
            validation_status="evidence_found",
            supporting_excerpt="Python in project entry.",
            relevance_explanation="The resume explicitly mentions Python.",
        ))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_database_url_missing_invalid_and_postgres_normalization(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database.get_database_url() is None
    with pytest.raises(database.DatabaseConfigurationError, match="not configured") as missing:
        database.create_database_engine()
    assert "password" not in str(missing.value)

    monkeypatch.setenv("DATABASE_URL", "not a connection string")
    with pytest.raises(database.DatabaseConfigurationError, match="invalid") as invalid:
        database.get_database_url()
    assert "not a connection string" not in str(invalid.value)

    monkeypatch.setenv("DATABASE_URL", "postgresql://skillfair:do-not-print@localhost:5432/skillfair")
    normalized = database.get_database_url()
    assert normalized is not None and normalized.startswith("postgresql+psycopg://")
    assert "do-not-print" not in make_url(normalized).render_as_string(hide_password=True)
    monkeypatch.setenv("SKILLFAIR_MIGRATION_DATABASE_URL", "postgresql+psycopg://migrator:private@localhost:5433/skillfair_db")
    migration_url = database.get_migration_database_url()
    assert migration_url is not None
    assert make_url(migration_url).username == "migrator"
    assert make_url(migration_url).database == "skillfair_db"
    assert "private" not in make_url(migration_url).render_as_string(hide_password=True)
    monkeypatch.delenv("SKILLFAIR_MIGRATION_DATABASE_URL", raising=False)
    assert database.get_migration_database_url() is None
    connection_events = []
    original_create_engine = database.create_engine

    def spy_create_engine(*args, **kwargs):
        engine = original_create_engine(*args, **kwargs)
        event.listen(engine, "connect", lambda *_: connection_events.append("opened"))
        return engine

    monkeypatch.setattr(database, "create_engine", spy_create_engine)
    engine = database.create_database_engine(database_url="sqlite+pysqlite:///:memory:")
    assert connection_events == []
    engine.dispose()

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(HTTPException) as unavailable:
        next(database.get_db_session())
    assert unavailable.value.status_code == 503
    assert "DATABASE_URL" not in str(unavailable.value.detail)


def test_database_session_dependency_rolls_back_and_closes_on_failure(monkeypatch):
    from sqlalchemy.orm import sessionmaker

    database.dispose_database_engine()
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    engine = database.get_database_engine()
    Base.metadata.create_all(engine)

    class TrackingSession(Session):
        was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    monkeypatch.setattr(
        database,
        "create_session_factory",
        lambda bind: sessionmaker(bind=bind, class_=TrackingSession, autoflush=False, expire_on_commit=False),
    )
    dependency = database.get_db_session()
    session = next(dependency)
    user_id = uuid4()
    session.add(User(id=user_id, email_normalized="rollback@example.test", password_hash="$argon2id$example"))

    with pytest.raises(RuntimeError, match="test failure"):
        dependency.throw(RuntimeError("test failure"))

    assert not session.in_transaction()
    assert session.was_closed
    with Session(engine) as verification:
        assert verification.scalar(select(User).where(User.id == user_id)) is None

    normal_dependency = database.get_db_session()
    normal_session = next(normal_dependency)
    with pytest.raises(StopIteration):
        next(normal_dependency)
    assert normal_session.was_closed
    database.dispose_database_engine()


def test_database_health_reports_unconfigured_reachable_and_unavailable(monkeypatch, sqlite_engine: Engine):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    status = main.database_health()
    assert status.status == "unconfigured"
    assert status.configured is False and status.reachable is False

    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite:///:memory:")
    database.dispose_database_engine()
    assert database.check_database_health().status == "reachable"
    database.dispose_database_engine()

    class FailingEngine:
        def connect(self):
            raise OperationalError("connect", {}, Exception("private driver detail"))

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@localhost:5432/db")
    monkeypatch.setattr(database, "get_database_engine", lambda: FailingEngine())
    failed = database.check_database_health()
    assert failed.status == "unavailable"
    assert failed.configured is True and failed.reachable is False
    assert "secret" not in failed.model_dump_json()
    assert "private driver detail" not in failed.model_dump_json()
