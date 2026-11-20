from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import database, main
from app.auth import CSRF_COOKIE_NAME
from app.db_models import AnalysisSkill, Base, ResumeAnalysis, User


ORIGIN = "http://localhost:5174"
PASSWORD = "Navigation-test-password-93847"


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
    with TestClient(main.app, client=(f"testclient-{uuid4().hex}", 50000)) as client:
        yield client, engine
    if previous is None:
        main.app.dependency_overrides.pop(database.get_db_session, None)
    else:
        main.app.dependency_overrides[database.get_db_session] = previous
    engine.dispose()


def _register(client: TestClient, email: str) -> None:
    assert client.post("/api/auth/register", headers={"Origin": ORIGIN}, json={
        "email": email, "password": PASSWORD,
    }).status_code == 202
    assert client.post("/api/auth/login", headers={"Origin": ORIGIN}, json={
        "email": email, "password": PASSWORD,
    }).status_code == 200


def _csrf(client: TestClient) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    assert token
    return {"Origin": ORIGIN, "X-CSRF-Token": token}


def _seed_analysis(engine, email: str, skill: str, excerpt: str) -> tuple[str, object]:
    with Session(engine) as session:
        user = session.query(User).filter_by(email_normalized=email).one()
        row = ResumeAnalysis(public_id=uuid4().hex, owner_user_id=user.id, analysis_mode="local")
        session.add(row)
        session.flush()
        session.add(AnalysisSkill(
            analysis_id=row.id, owner_user_id=user.id, skill_name=skill,
            evidence_type="explicit", detection_method="local", validation_status="evidence_found",
            supporting_excerpt=excerpt, relevance_explanation="Exact source mention.",
        ))
        session.commit()
        return row.public_id, user.id


def _create_workflow(client: TestClient, analysis_id: str, job_title: str) -> dict:
    response = client.post("/api/career/orchestrator/runs", headers=_csrf(client), json={
        "analysis_id": analysis_id, "job_title": job_title,
        "job_description": "A private role description that must not be searched.",
        "essential_skills": ["Python"], "preferred_skills": [],
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_search_is_authenticated_bounded_owner_scoped_and_excludes_sensitive_text(app_client):
    client, engine = app_client
    assert client.get("/api/search?q=python").status_code == 401
    _register(client, "search-owner@example.test")
    analysis_id, _ = _seed_analysis(engine, "search-owner@example.test", "Python", "private@example.test used Python")
    workflow = _create_workflow(client, analysis_id, "Searchable Data Analyst")
    audit = client.post("/api/jobs/audits", headers=_csrf(client), json={
        "job_title": "Searchable Auditor", "job_description": "Private body text.", "requirements": [],
    })
    assert audit.status_code == 200

    response = client.get("/api/search?q=Searchable")
    assert response.status_code == 200
    found = response.json()["results"]
    assert {item["record_type"] for item in found} == {"workflow", "bias_audit"}
    assert {item["record_id"] for item in found} == {workflow["run_id"], audit.json()["audit_id"]}
    assert "Private body text" not in response.text
    assert "private@example.test" not in response.text
    assert "job_description" not in response.text
    assert client.get("/api/search?q=x").status_code == 422
    assert client.get("/api/search?q=" + "a" * 81).status_code == 422

    other = TestClient(main.app, client=(f"testclient-{uuid4().hex}", 50000))
    _register(other, "search-other@example.test")
    assert other.get("/api/search?q=Searchable").json()["results"] == []
    other.close()


def test_search_returns_saved_skill_label_and_exact_analysis_is_owner_scoped(app_client):
    client, engine = app_client
    _register(client, "analysis-search@example.test")
    analysis_id, _ = _seed_analysis(engine, "analysis-search@example.test", "PostgreSQL", "private evidence excerpt")
    results = client.get("/api/search?q=PostgreSQL").json()["results"]
    assert len(results) == 1 and results[0]["record_type"] == "analysis"
    assert results[0]["record_id"] == analysis_id
    assert "private evidence excerpt" not in str(results)
    exact = client.get(f"/api/skills/analyses/{analysis_id}")
    assert exact.status_code == 200
    assert exact.json()["analysis_id"] == analysis_id
    assert exact.json()["skills"][0]["supporting_excerpt"] == "private evidence excerpt"

    other = TestClient(main.app, client=(f"testclient-{uuid4().hex}", 50000))
    _register(other, "analysis-search-other@example.test")
    assert other.get(f"/api/skills/analyses/{analysis_id}").status_code == 404
    other.close()


def test_notifications_are_created_from_committed_review_events_owned_and_csrf_protected(app_client):
    client, engine = app_client
    anonymous = client.get("/api/notifications")
    assert anonymous.status_code == 401
    _register(client, "notifications-owner@example.test")
    analysis_id, _ = _seed_analysis(engine, "notifications-owner@example.test", "Python", "Used Python in a project.")
    workflow = _create_workflow(client, analysis_id, "Notification Data Analyst")
    empty = client.get("/api/notifications")
    assert empty.status_code == 200 and empty.json() == {"notifications": [], "unread_count": 0}
    review_url = f"/api/career/orchestrator/runs/{workflow['run_id']}/review"
    payload = {"decision": "proceed", "reviewed_requirement_ids": workflow["human_review"]["reviewable_requirement_ids"]}
    assert client.post(review_url, headers={"Origin": ORIGIN}, json=payload).status_code == 403
    review = client.post(review_url, headers=_csrf(client), json=payload)
    assert review.status_code == 200
    assert review.json()["learning_pathways"]["status"] == "complete"
    repeated = client.post(review_url, headers=_csrf(client), json=payload)
    assert repeated.status_code == 200

    listing = client.get("/api/notifications?limit=10")
    assert listing.status_code == 200
    body = listing.json()
    assert body["unread_count"] == 2
    assert {item["notification_type"] for item in body["notifications"]} == {"workflow_decision", "learning_pathways_ready"}
    assert all(item["workflow_id"] == workflow["run_id"] for item in body["notifications"])
    assert "owner_user_id" not in listing.text and "source_event_key" not in listing.text
    notification_id = body["notifications"][0]["notification_id"]

    other = TestClient(main.app, client=(f"testclient-{uuid4().hex}", 50000))
    _register(other, "notifications-other@example.test")
    assert other.get("/api/notifications").json() == {"notifications": [], "unread_count": 0}
    denied = other.post(f"/api/notifications/{notification_id}/read", headers=_csrf(other))
    assert denied.status_code == 404
    other.close()

    marked = client.post(f"/api/notifications/{notification_id}/read", headers=_csrf(client))
    assert marked.status_code == 200 and marked.json()["read_at"] is not None
    assert client.post(f"/api/notifications/{notification_id}/read", headers=_csrf(client)).status_code == 200
    assert client.get("/api/notifications").json()["unread_count"] == 1


@pytest.mark.parametrize("decision", ["revise", "stop"])
def test_non_proceed_decisions_create_no_learning_pathway_notification(app_client, decision):
    client, engine = app_client
    _register(client, f"{decision}-notifications@example.test")
    analysis_id, _ = _seed_analysis(engine, f"{decision}-notifications@example.test", "Python", "Used Python in a project.")
    workflow = _create_workflow(client, analysis_id, f"{decision.title()} Notification Review")
    response = client.post(
        f"/api/career/orchestrator/runs/{workflow['run_id']}/review",
        headers=_csrf(client),
        json={"decision": decision, "reviewed_requirement_ids": workflow["human_review"]["reviewable_requirement_ids"]},
    )
    assert response.status_code == 200
    notifications = client.get("/api/notifications").json()["notifications"]
    assert len(notifications) == 1
    assert notifications[0]["notification_type"] == "workflow_decision"
    assert decision.capitalize() in notifications[0]["message"]
