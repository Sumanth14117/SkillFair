from __future__ import annotations

from datetime import datetime, timezone
from collections import OrderedDict
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import database, main
from app.auth import CSRF_COOKIE_NAME
from app.db_models import AnalysisSkill, Base, CareerWorkflow, ResumeAnalysis, User, WorkflowReview
from app.models import SkillEvidence, SkillsDiscoverResponse
from app.persistence import _linked_analysis


ORIGIN = "http://localhost:5174"
PASSWORD = "Persistent-test-password-93847"


def test_linked_analysis_lookup_requires_matching_owner():
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    other_owner_id = uuid4()
    with Session(engine) as session:
        session.add_all([
            User(id=owner_id, email_normalized="linked-owner@example.test", password_hash="$argon2id$test"),
            User(id=other_owner_id, email_normalized="other-owner@example.test", password_hash="$argon2id$test"),
        ])
        session.flush()
        analysis = ResumeAnalysis(public_id="owner-scoped-analysis", owner_user_id=owner_id, analysis_mode="local")
        session.add(analysis)
        session.flush()

        assert _linked_analysis(session, analysis.id, owner_id) is analysis
        assert _linked_analysis(session, analysis.id, other_owner_id) is None
    engine.dispose()


@pytest.fixture
def app_client(monkeypatch):
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    previous = main.app.dependency_overrides.get(database.get_db_session)
    main.app.dependency_overrides[database.get_db_session] = override_session
    monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setenv("SKILLFAIR_AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("SKILLFAIR_AUTH_RATE_LIMIT_PER_MINUTE", "1000")
    with TestClient(main.app) as client:
        yield client, engine
    if previous is None:
        main.app.dependency_overrides.pop(database.get_db_session, None)
    else:
        main.app.dependency_overrides[database.get_db_session] = previous
    engine.dispose()


def _register(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 202, response.text
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert login.status_code == 200, login.text


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token
    return {"Origin": ORIGIN, "X-CSRF-Token": token}


def _create_analysis(client: TestClient, monkeypatch, label: str) -> dict:
    skill = SkillEvidence(
        skill_name="Python",
        evidence_type="explicit",
        detection_method="local",
        supporting_excerpt=f"Built {label} reports using Python.",
        relevance_explanation="The exact Python mention appears in the resume's Projects section; this is not proof of proficiency.",
        validation_status="evidence_found",
    )
    monkeypatch.setattr(main, "validate_and_extract", lambda *_args: f"private raw text for {label}")
    monkeypatch.setattr(
        main,
        "analyze_skills",
        lambda *_args, **_kwargs: SkillsDiscoverResponse(
            analysis_mode="local",
            message="Local deterministic analysis completed.",
            extracted_characters=32,
            skills=[skill],
        ),
    )
    response = client.post(
        "/api/skills/discover",
        headers=_csrf(client),
        data={"analysis_mode": "local"},
        files={"file": ("resume.pdf", b"%PDF-test", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "private raw text" not in response.text
    return body


def _create_workflow(client: TestClient, analysis_id: str) -> dict:
    response = client.post(
        "/api/career/orchestrator/runs",
        headers=_csrf(client),
        json={
            "analysis_id": analysis_id,
            "job_title": "Data Analyst",
            "job_description": "Use Python to create clear operational reports.",
            "geography": "Remote",
            "essential_skills": ["Python"],
            "preferred_skills": ["SQL"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_workflow_history_is_newest_first_account_scoped_and_includes_persisted_timestamps(app_client, monkeypatch):
    client, engine = app_client
    _register(client, "history-owner@example.test")
    analysis = _create_analysis(client, monkeypatch, "history")
    older = _create_workflow(client, analysis["analysis_id"])
    newer = _create_workflow(client, analysis["analysis_id"])
    older_created = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    newer_created = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)

    with Session(engine) as session:
        rows = session.scalars(select(CareerWorkflow).where(
            CareerWorkflow.public_id.in_([older["run_id"], newer["run_id"]])
        )).all()
        for row in rows:
            if row.public_id == older["run_id"]:
                row.created_at = older_created
                row.updated_at = older_created
            else:
                row.created_at = newer_created
                row.updated_at = newer_created
        session.commit()

    response = client.get("/api/career/orchestrator/runs")
    assert response.status_code == 200
    history = response.json()
    assert [item["run_id"] for item in history] == [newer["run_id"], older["run_id"]]
    assert [item["job_title"] for item in history] == ["Data Analyst", "Data Analyst"]
    assert history[0]["created_at"].startswith("2026-01-02T10:00:00")
    assert history[0]["updated_at"].startswith("2026-01-02T10:00:00")
    assert history[0]["workflow"]["workflow_status"] == "awaiting_human_review"
    assert "owner_user_id" not in history[0]
    assert "id" not in history[0]

    other = TestClient(main.app)
    _register(other, "history-empty@example.test")
    empty = other.get("/api/career/orchestrator/runs")
    assert empty.status_code == 200
    assert empty.json() == []
    other.close()

    anonymous = TestClient(main.app)
    unauthenticated = anonymous.get("/api/career/orchestrator/runs")
    assert unauthenticated.status_code == 401
    anonymous.close()


def test_analysis_persists_validated_evidence_and_is_owner_scoped(app_client, monkeypatch):
    client, engine = app_client
    _register(client, "owner-a@example.test")
    created = _create_analysis(client, monkeypatch, "project")

    latest = client.get("/api/skills/analyses/latest")
    assert latest.status_code == 200
    assert latest.json()["analysis_id"] == created["analysis_id"]
    assert latest.json()["skills"][0]["supporting_excerpt"] == "Built project reports using Python."
    assert "private raw text" not in latest.text
    matched = client.post("/api/jobs/match", json={
        "analysis_id": created["analysis_id"],
        "job_title": "Data Analyst",
        "job_description": "Use Python to prepare reports.",
        "required_skills": ["Python"],
    })
    assert matched.status_code == 200
    assert matched.json()["matched_skills"][0]["supporting_excerpt"] == "Built project reports using Python."
    inclusive = client.post("/api/jobs/inclusive-match", json={
        "analysis_id": created["analysis_id"],
        "job_title": "Data Analyst",
        "job_description": "Use Python to prepare reports.",
        "essential_skills": ["Python"],
        "preferred_skills": [],
    })
    assert inclusive.status_code == 200
    assert inclusive.json()["requirements"][0]["evidence"][0]["supporting_excerpt"] == "Built project reports using Python."

    with Session(engine) as session:
        analysis = session.scalar(select(ResumeAnalysis).where(ResumeAnalysis.public_id == created["analysis_id"]))
        assert analysis is not None
        assert session.scalar(select(AnalysisSkill).where(AnalysisSkill.analysis_id == analysis.id)) is not None
        assert "private raw text" not in str(analysis.public_id)
        assert session.scalar(select(User).where(User.id == analysis.owner_user_id)) is not None

    other = TestClient(main.app)
    _register(other, "owner-b@example.test")
    assert other.get("/api/skills/analyses/latest").json() is None
    assert other.post("/api/jobs/match", json={
        "analysis_id": created["analysis_id"],
        "job_title": "Data Analyst",
        "job_description": "Use Python.",
        "required_skills": ["Python"],
    }).status_code == 404
    other.close()


def test_workflow_review_survives_fresh_sessions_and_is_owner_scoped(app_client, monkeypatch):
    client, engine = app_client
    _register(client, "workflow-owner@example.test")
    analysis = _create_analysis(client, monkeypatch, "portfolio")
    workflow = _create_workflow(client, analysis["analysis_id"])
    run_id = workflow["run_id"]
    requirement_ids = workflow["human_review"]["reviewable_requirement_ids"]

    # Dropping the previous process-local stores simulates a server restart; a
    # separate request then uses a fresh dependency-provided SQLAlchemy session.
    from app.career_orchestrator import career_workflow_store
    from app.provenance import analysis_store

    monkeypatch.setattr(analysis_store, "_records", OrderedDict())
    monkeypatch.setattr(career_workflow_store, "_records", OrderedDict())
    fetched = client.get(f"/api/career/orchestrator/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["human_review"]["status"] == "pending"
    listed = client.get("/api/career/orchestrator/runs")
    assert listed.status_code == 200
    assert listed.json()[0]["workflow"]["run_id"] == run_id
    assert listed.json()[0]["job_title"] == "Data Analyst"

    started = client.post(f"/api/impact/reviews/{run_id}/start", headers=_csrf(client))
    assert started.status_code == 200

    proceeded = client.post(
        f"/api/career/orchestrator/runs/{run_id}/review",
        headers=_csrf(client),
        json={"decision": "proceed", "reviewed_requirement_ids": requirement_ids, "review_notes": "Not persisted"},
    )
    assert proceeded.status_code == 200, proceeded.text
    assert proceeded.json()["workflow_status"] == "complete"
    assert proceeded.json()["learning_pathways"]["status"] == "complete"

    restored = client.get(f"/api/career/orchestrator/runs/{run_id}")
    assert restored.status_code == 200
    assert restored.json()["human_review"]["decision"] == "proceed"
    assert restored.json()["human_review"]["reviewed_requirement_ids"] == requirement_ids
    assert restored.json()["human_review"]["review_notes"] is None
    assert restored.json()["learning_pathways"]["result"] is not None

    with Session(engine) as session:
        row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == run_id))
        review = session.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == row.id))
        assert review.review_started_at is not None
        assert review.review_completed_at is not None
        assert review.decision == "proceed"
        assert row.owner_user_id == review.owner_user_id
        assert row.version == 2
        assert isinstance(review.review_completed_at, datetime)
        assert review.review_completed_at.tzinfo is None or review.review_completed_at.tzinfo == timezone.utc

    other = TestClient(main.app)
    _register(other, "workflow-other@example.test")
    assert other.get(f"/api/career/orchestrator/runs/{run_id}").status_code == 404
    denied_review = other.post(
        f"/api/career/orchestrator/runs/{run_id}/review",
        headers=_csrf(other),
        json={"decision": "stop", "reviewed_requirement_ids": requirement_ids},
    )
    assert denied_review.status_code == 404
    other.close()


def test_persisted_review_conflicts_are_rejected_and_duplicates_are_idempotent(app_client, monkeypatch):
    client, _engine = app_client
    _register(client, "review-owner@example.test")
    analysis = _create_analysis(client, monkeypatch, "portfolio")
    workflow = _create_workflow(client, analysis["analysis_id"])
    path = f"/api/career/orchestrator/runs/{workflow['run_id']}/review"
    payload = {"decision": "revise", "reviewed_requirement_ids": workflow["human_review"]["reviewable_requirement_ids"]}
    first = client.post(path, headers=_csrf(client), json=payload)
    repeated = client.post(path, headers=_csrf(client), json=payload)
    conflict = client.post(path, headers=_csrf(client), json={**payload, "decision": "stop"})
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert conflict.status_code == 409
    assert client.get(f"/api/career/orchestrator/runs/{workflow['run_id']}").json()["learning_pathways"]["result"] is None


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("revise", "revise_required"), ("stop", "stopped")],
)
def test_persisted_revise_and_stop_never_create_learning_pathways(app_client, monkeypatch, decision, expected_status):
    client, _engine = app_client
    _register(client, f"{decision}-owner@example.test")
    analysis = _create_analysis(client, monkeypatch, decision)
    workflow = _create_workflow(client, analysis["analysis_id"])
    result = client.post(
        f"/api/career/orchestrator/runs/{workflow['run_id']}/review",
        headers=_csrf(client),
        json={"decision": decision, "reviewed_requirement_ids": workflow["human_review"]["reviewable_requirement_ids"]},
    )
    assert result.status_code == 200
    assert result.json()["workflow_status"] == expected_status
    assert result.json()["learning_pathways"]["result"] is None
    restored = client.get(f"/api/career/orchestrator/runs/{workflow['run_id']}")
    assert restored.status_code == 200
    assert restored.json()["human_review"]["decision"] == decision
    assert restored.json()["learning_pathways"]["result"] is None
