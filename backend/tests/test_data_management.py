from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import database, main
from app.auth import CSRF_COOKIE_NAME
from app.db_models import (
    AnalysisSkill,
    Base,
    CandidateFeedback,
    CareerWorkflow,
    ImpactObservation,
    RecruiterFeedback,
    ResumeAnalysis,
    User,
    UserSession,
    WorkflowReview,
)


ORIGIN = "http://localhost:5174"
PASSWORD = "Data-management-test-password-5492"


@pytest.fixture
def data_client(monkeypatch, tmp_path):
    engine = database.create_database_engine(f"sqlite+pysqlite:///{tmp_path / 'data-management.sqlite'}")
    Base.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    previous = main.app.dependency_overrides.get(database.get_db_session)
    main.app.dependency_overrides[database.get_db_session] = override_session
    monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setenv("SKILLFAIR_AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("SKILLFAIR_AUTH_RATE_LIMIT_PER_MINUTE", "1000")
    clients: list[TestClient] = []

    def client() -> TestClient:
        value = TestClient(main.app, client=(f"data-test-{uuid4().hex}", 50000))
        clients.append(value)
        return value

    try:
        yield client, engine
    finally:
        for value in clients:
            value.close()
        if previous is None:
            main.app.dependency_overrides.pop(database.get_db_session, None)
        else:
            main.app.dependency_overrides[database.get_db_session] = previous
        engine.dispose()


def _signed_in(client: TestClient, email: str) -> None:
    registration = client.post("/api/auth/register", json={"email": email, "password": PASSWORD}, headers={"Origin": ORIGIN})
    assert registration.status_code == 202, registration.text
    login = client.post("/api/auth/login", json={"email": email, "password": PASSWORD}, headers={"Origin": ORIGIN})
    assert login.status_code == 200, login.text


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token
    return {"Origin": ORIGIN, "X-CSRF-Token": token}


def _user_id(engine, email: str):
    with Session(engine) as db:
        return db.scalar(select(User.id).where(User.email_normalized == email))


def _analysis(db: Session, owner_id, *, public_id: str | None = None, expires_at=None) -> ResumeAnalysis:
    row = ResumeAnalysis(public_id=public_id or f"analysis-{uuid4().hex}", owner_user_id=owner_id, analysis_mode="local", expires_at=expires_at)
    db.add(row)
    db.flush()
    db.add(AnalysisSkill(
        analysis_id=row.id, owner_user_id=owner_id, skill_name="Python", evidence_type="explicit",
        detection_method="local", validation_status="evidence_found", supporting_excerpt="Built with Python; contact person@example.test.",
        relevance_explanation="An exact mention; not proof of proficiency.",
    ))
    return row


def _workflow(db: Session, owner_id, analysis: ResumeAnalysis, *, public_id: str | None = None, status="awaiting_human_review", expires_at=None) -> CareerWorkflow:
    decisions = {"complete": "proceed", "revise_required": "revise", "stopped": "stop"}
    row = CareerWorkflow(
        public_id=public_id or f"workflow-{uuid4().hex}", owner_user_id=owner_id, analysis_id=analysis.id,
        job_title="Backend Engineer", job_description="Use Python for services.", geography="Remote",
        essential_skills=["Python"], preferred_skills=[], workflow_status=status,
        reviewable_requirement_ids=[], stages_snapshot=[], skill_passport_snapshot={}, market_intelligence_snapshot={},
        job_audit_snapshot={}, inclusive_matching_snapshot={}, learning_pathways_snapshot={}, workflow_summary="Saved workflow.",
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    decision = decisions.get(status)
    db.add(WorkflowReview(
        workflow_id=row.id, owner_user_id=owner_id, decision=decision,
        reviewed_requirement_ids=[], review_started_at=None,
        review_completed_at=datetime.now(timezone.utc) if decision else None,
    ))
    db.flush()
    return row


def test_account_data_and_export_require_authentication(data_client):
    make_client, _ = data_client
    anonymous = make_client()
    assert anonymous.get("/api/account/data").status_code == 401
    assert anonymous.get("/api/account/export").status_code == 401
    assert anonymous.delete(f"/api/account/analyses/{'a' * 32}", headers={"Origin": ORIGIN}).status_code == 401
    assert anonymous.delete(f"/api/account/workflows/{'w' * 32}", headers={"Origin": ORIGIN}).status_code == 401


def test_export_contains_owned_data_but_no_authentication_material(data_client):
    make_client, engine = data_client
    client = make_client()
    _signed_in(client, "export-owner@example.test")
    owner_id = _user_id(engine, "export-owner@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id)
        workflow = _workflow(db, owner_id, analysis, status="complete")
        review = db.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == workflow.id))
        db.add(RecruiterFeedback(
            owner_user_id=owner_id, workflow_id=workflow.id, submission_id=uuid4(), consent_to_aggregate=True,
            evidence_usefulness=4, explanation_clarity=5, review_ease=3,
        ))
        db.add(ImpactObservation(
            owner_user_id=owner_id, workflow_review_id=review.id, submission_id=None,
            observation_type="review_duration", numeric_value=120,
        ))
        db.commit()

    response = client.get("/api/account/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"].endswith('filename="skillfair-account-export.json"')
    body = response.json()
    assert body["format_version"] == "1.0"
    assert body["account"]["email"] == "export-owner@example.test"
    assert body["analyses"][0]["skills"][0]["supporting_excerpt"] == "Built with Python; contact [REDACTED EMAIL]."
    assert "person@example.test" not in response.text
    assert body["workflows"][0]["review"]["decision"] == "proceed"
    assert body["recruiter_feedback"][0]["evidence_usefulness"] == 4
    assert body["impact_observations"][0]["numeric_value"] == 120
    assert "password_hash" not in response.text
    assert "token_hash" not in response.text
    assert "csrf_token_hash" not in response.text
    assert "session" not in body
    assert "owner_user_id" not in response.text


def test_export_isolated_between_accounts(data_client):
    make_client, engine = data_client
    owner = make_client()
    other = make_client()
    _signed_in(owner, "export-a@example.test")
    _signed_in(other, "export-b@example.test")
    with Session(engine) as db:
        _analysis(db, _user_id(engine, "export-a@example.test"))
        db.commit()
    body = other.get("/api/account/export").json()
    assert body["account"]["email"] == "export-b@example.test"
    assert body["analyses"] == []
    assert "export-a@example.test" not in str(body)


def test_account_data_is_metadata_only_paged_and_reports_expired_records(data_client):
    make_client, engine = data_client
    client = make_client()
    _signed_in(client, "inventory-owner@example.test")
    owner_id = _user_id(engine, "inventory-owner@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        _workflow(db, owner_id, analysis, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        db.commit()
    response = client.get("/api/account/data?limit=1&offset=0")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["analysis_total"] == body["workflow_total"] == 1
    assert body["analyses"][0]["status"] == "expired"
    assert body["workflows"][0]["status"] == "expired"
    assert "supporting_excerpt" not in response.text
    assert "job_description" not in response.text
    workflow_id = body["workflows"][0]["run_id"]
    analysis_id = body["analyses"][0]["analysis_id"]
    assert client.delete(f"/api/account/workflows/{workflow_id}", headers=_csrf(client)).status_code == 204
    assert client.delete(f"/api/account/analyses/{analysis_id}", headers=_csrf(client)).status_code == 204


def test_analysis_delete_is_blocked_until_workflows_are_deleted_then_cascades_only_linked_records(data_client):
    make_client, engine = data_client
    client = make_client()
    _signed_in(client, "delete-owner@example.test")
    owner_id = _user_id(engine, "delete-owner@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id)
        workflow = _workflow(db, owner_id, analysis, status="complete")
        review = db.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == workflow.id))
        analysis_public_id, workflow_public_id = analysis.public_id, workflow.public_id
        review_id = review.id
        db.add(CandidateFeedback(
            owner_user_id=owner_id, analysis_id=analysis.id, submission_id=uuid4(), consent_to_aggregate=True,
            skill_representation_clarity=4, understanding_of_skills=4, perceived_transparency=4, overall_experience=5,
        ))
        db.add(RecruiterFeedback(
            owner_user_id=owner_id, workflow_id=workflow.id, submission_id=uuid4(), consent_to_aggregate=True,
            evidence_usefulness=4, explanation_clarity=4, review_ease=4,
        ))
        db.add_all([
            ImpactObservation(owner_user_id=owner_id, workflow_review_id=review.id, observation_type="review_duration", numeric_value=90),
            ImpactObservation(owner_user_id=owner_id, workflow_review_id=None, submission_id=uuid4(), observation_type="baseline_duration", numeric_value=120),
        ])
        db.commit()

    blocked = client.delete(f"/api/account/analyses/{analysis_public_id}", headers=_csrf(client))
    assert blocked.status_code == 409
    removed_workflow = client.delete(f"/api/account/workflows/{workflow_public_id}", headers=_csrf(client))
    assert removed_workflow.status_code == 204
    with Session(engine) as db:
        assert db.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == workflow_public_id)) is None
        assert db.scalar(select(WorkflowReview).where(WorkflowReview.id == review_id)) is None
        assert db.scalar(select(RecruiterFeedback).where(RecruiterFeedback.owner_user_id == owner_id)) is None
        assert db.scalar(select(ImpactObservation).where(ImpactObservation.workflow_review_id == review_id)) is None
        assert db.scalar(select(ImpactObservation).where(ImpactObservation.observation_type == "baseline_duration")) is not None
        assert db.scalar(select(ResumeAnalysis).where(ResumeAnalysis.public_id == analysis_public_id)) is not None

    removed_analysis = client.delete(f"/api/account/analyses/{analysis_public_id}", headers=_csrf(client))
    assert removed_analysis.status_code == 204
    with Session(engine) as db:
        assert db.scalar(select(ResumeAnalysis).where(ResumeAnalysis.public_id == analysis_public_id)) is None
        assert db.scalar(select(AnalysisSkill).where(AnalysisSkill.owner_user_id == owner_id)) is None
        assert db.scalar(select(CandidateFeedback).where(CandidateFeedback.owner_user_id == owner_id)) is None


@pytest.mark.parametrize("status", ["awaiting_human_review", "complete", "revise_required", "stopped"])
def test_workflows_in_every_state_can_be_deleted(data_client, status):
    make_client, engine = data_client
    client = make_client()
    _signed_in(client, f"delete-{status}@example.test")
    owner_id = _user_id(engine, f"delete-{status}@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id)
        workflow = _workflow(db, owner_id, analysis, status=status)
        public_id = workflow.public_id
        db.commit()
    response = client.delete(f"/api/account/workflows/{public_id}", headers=_csrf(client))
    assert response.status_code == 204
    assert client.delete(f"/api/account/workflows/{public_id}", headers=_csrf(client)).status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == public_id)) is None


def test_cross_account_deletions_return_same_not_found_and_preserve_records(data_client):
    make_client, engine = data_client
    owner, other = make_client(), make_client()
    _signed_in(owner, "record-owner@example.test")
    _signed_in(other, "record-other@example.test")
    owner_id = _user_id(engine, "record-owner@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id)
        workflow = _workflow(db, owner_id, analysis)
        analysis_id, workflow_id = analysis.public_id, workflow.public_id
        db.commit()
    assert other.delete(f"/api/account/analyses/{analysis_id}", headers=_csrf(other)).status_code == 404
    assert other.delete(f"/api/account/workflows/{workflow_id}", headers=_csrf(other)).status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(ResumeAnalysis).where(ResumeAnalysis.public_id == analysis_id)) is not None
        assert db.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == workflow_id)) is not None


def test_delete_requires_csrf_and_does_not_change_data_without_it(data_client):
    make_client, engine = data_client
    client = make_client()
    _signed_in(client, "csrf-delete@example.test")
    owner_id = _user_id(engine, "csrf-delete@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id)
        public_id = analysis.public_id
        db.commit()
    response = client.delete(f"/api/account/analyses/{public_id}", headers={"Origin": ORIGIN})
    assert response.status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(ResumeAnalysis).where(ResumeAnalysis.public_id == public_id)) is not None


def test_database_failure_rolls_back_workflow_deletion(data_client, monkeypatch):
    make_client, engine = data_client
    client = make_client()
    _signed_in(client, "rollback-owner@example.test")
    owner_id = _user_id(engine, "rollback-owner@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id)
        workflow = _workflow(db, owner_id, analysis)
        public_id = workflow.public_id
        db.commit()

    from app import data_management
    original_delete = main.delete_workflow

    def mark_workflow_delete(db, authenticated_owner, requested_id):
        db.info["test_fail_workflow_delete_commit"] = True
        return data_management.delete_workflow(db, authenticated_owner, requested_id)

    monkeypatch.setattr(main, "delete_workflow", mark_workflow_delete)

    def fail_before_commit(session):
        if session.info.pop("test_fail_workflow_delete_commit", False):
            event.remove(Session, "before_commit", fail_before_commit)
            raise SQLAlchemyError("suppressed test database failure")

    event.listen(Session, "before_commit", fail_before_commit)
    try:
        response = client.delete(f"/api/account/workflows/{public_id}", headers=_csrf(client))
    finally:
        if event.contains(Session, "before_commit", fail_before_commit):
            event.remove(Session, "before_commit", fail_before_commit)
        monkeypatch.setattr(main, "delete_workflow", original_delete)
    assert response.status_code == 503
    with Session(engine) as db:
        assert db.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == public_id)) is not None
        assert db.execute(select(1)).scalar_one() == 1


def test_concurrent_duplicate_workflow_deletions_have_one_winner(data_client, monkeypatch):
    make_client, engine = data_client
    first, second = make_client(), make_client()
    _signed_in(first, "concurrent-delete@example.test")
    _signed_in(second, "concurrent-delete@example.test")
    owner_id = _user_id(engine, "concurrent-delete@example.test")
    with Session(engine) as db:
        analysis = _analysis(db, owner_id)
        workflow = _workflow(db, owner_id, analysis)
        public_id = workflow.public_id
        db.commit()

    barrier = Barrier(2)
    from app import data_management
    original = data_management.delete_workflow

    def synchronized_delete(db, authenticated_owner, requested_id):
        barrier.wait(timeout=5)
        return original(db, authenticated_owner, requested_id)

    monkeypatch.setattr(data_management, "delete_workflow", synchronized_delete)
    def remove(client):
        return client.delete(f"/api/account/workflows/{public_id}", headers=_csrf(client)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(remove, (first, second)))
    assert results == [204, 404]
    with Session(engine) as db:
        assert db.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == public_id)) is None

