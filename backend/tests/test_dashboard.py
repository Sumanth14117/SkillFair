from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import database, main
from app.auth import AuthContext, get_auth_context
from app.db_models import Base, CareerWorkflow, ResumeAnalysis, User, WorkflowReview


@pytest.fixture
def dashboard_client(monkeypatch):
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner_a = User(email_normalized="overview-a@example.test", password_hash="$argon2id$test")
        owner_b = User(email_normalized="overview-b@example.test", password_hash="$argon2id$test")
        db.add_all([owner_a, owner_b])
        db.commit()
        db.refresh(owner_a)
        db.refresh(owner_b)

    def override_db():
        with Session(engine) as session:
            yield session

    auth_state = {"user": owner_a}

    def override_auth():
        return AuthContext(user=auth_state["user"], session=None)

    previous_db = main.app.dependency_overrides.get(database.get_db_session)
    previous_auth = main.app.dependency_overrides.get(get_auth_context)
    main.app.dependency_overrides[database.get_db_session] = override_db
    main.app.dependency_overrides[get_auth_context] = override_auth
    monkeypatch.setenv("SKILLFAIR_AUTH_RATE_LIMIT_PER_MINUTE", "1000")
    with TestClient(main.app, client=(f"dashboard-{uuid4().hex}", 50000), raise_server_exceptions=False) as client:
        yield client, engine, owner_a, owner_b, auth_state
    if previous_db is None:
        main.app.dependency_overrides.pop(database.get_db_session, None)
    else:
        main.app.dependency_overrides[database.get_db_session] = previous_db
    if previous_auth is None:
        main.app.dependency_overrides.pop(get_auth_context, None)
    else:
        main.app.dependency_overrides[get_auth_context] = previous_auth
    engine.dispose()


def add_workflow(db: Session, owner_id, run_id: str, title: str, status: str, created_at: datetime, decision: str | None, started_at: datetime | None, completed_at: datetime | None, include_review: bool = True):
    analysis = ResumeAnalysis(public_id=f"analysis-{run_id}", owner_user_id=owner_id, analysis_mode="local", created_at=created_at)
    db.add(analysis)
    db.flush()
    workflow = CareerWorkflow(
        public_id=run_id,
        owner_user_id=owner_id,
        analysis_id=analysis.id,
        job_title=title,
        job_description="Private job description must not be returned.",
        geography="Private geography",
        essential_skills=["Python"],
        preferred_skills=["SQL"],
        workflow_status=status,
        reviewable_requirement_ids=["essential:python"],
        stages_snapshot=[],
        skill_passport_snapshot={},
        market_intelligence_snapshot={},
        job_audit_snapshot={},
        inclusive_matching_snapshot={},
        learning_pathways_snapshot={"status": "blocked_pending_review", "result": None},
        workflow_summary="Private summary must not be returned.",
        created_at=created_at,
        updated_at=completed_at or started_at or created_at,
    )
    db.add(workflow)
    db.flush()
    if include_review:
        db.add(WorkflowReview(
            workflow_id=workflow.id,
            owner_user_id=owner_id,
            decision=decision,
            reviewed_requirement_ids=[] if decision is None else ["essential:python"],
            review_started_at=started_at,
            review_completed_at=completed_at,
        ))
    return workflow


def test_overview_is_authenticated_and_empty_response_uses_real_zero_counts(dashboard_client):
    client, _engine, _owner_a, _owner_b, _auth_state = dashboard_client
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == {
        "saved_analyses": 0,
        "total_workflows": 0,
        "pending_reviews": 0,
        "completed_reviews": 0,
    }
    assert body["workflow_status_distribution"] == []
    assert body["review_decision_distribution"] == []
    assert body["recent_workflows"] == []
    assert body["recent_activity"] == []
    assert "owner_user_id" not in response.text


def test_overview_counts_actual_statuses_and_orders_recent_activity(dashboard_client):
    client, engine, owner_a, _owner_b, _auth_state = dashboard_client
    older = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    started = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    completed = datetime(2026, 10, 2, 11, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        add_workflow(db, owner_a.id, "workflow-pending-dashboard", "Pending Analyst", "awaiting_human_review", older, None, started, None)
        add_workflow(db, owner_a.id, "workflow-complete-dashboard", "Completed Analyst", "complete", newer, "proceed", started, completed)
        add_workflow(db, owner_a.id, "workflow-revise-dashboard", "Revised Analyst", "revise_required", newer.replace(day=3), "revise", None, newer.replace(day=3, hour=10))
        db.commit()

    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == {
        "saved_analyses": 3,
        "total_workflows": 3,
        "pending_reviews": 1,
        "completed_reviews": 2,
    }
    assert {item["status"]: item["count"] for item in body["workflow_status_distribution"]} == {
        "awaiting_human_review": 1,
        "complete": 1,
        "revise_required": 1,
    }
    assert {item["decision"]: item["count"] for item in body["review_decision_distribution"]} == {
        "pending": 1,
        "proceed": 1,
        "revise": 1,
    }
    assert body["recent_workflows"][0]["job_title"] == "Revised Analyst"
    assert body["recent_workflows"][0]["workflow_status"] == "revise_required"
    activity_times = [datetime.fromisoformat(event["occurred_at"]) for event in body["recent_activity"]]
    assert activity_times == sorted(activity_times, reverse=True)
    assert any(event["event_type"] == "review_decided" and event["decision"] == "proceed" for event in body["recent_activity"])
    for private_field in ("analysis_id", "owner_user_id", "job_description", "geography", "workflow_summary"):
        assert private_field not in response.text
    assert "Private job description" not in response.text


def test_overview_aggregates_and_recent_records_are_account_scoped(dashboard_client):
    client, engine, owner_a, owner_b, auth_state = dashboard_client
    created = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        add_workflow(db, owner_a.id, "workflow-only-account-a", "Account A Analyst", "awaiting_human_review", created, None, None, None)
        add_workflow(db, owner_b.id, "workflow-only-account-b", "Account B Private Role", "stopped", created, "stop", None, created.replace(hour=10))
        db.commit()

    account_a = client.get("/api/dashboard/overview")
    assert account_a.status_code == 200
    assert account_a.json()["summary"]["total_workflows"] == 1
    assert [item["job_title"] for item in account_a.json()["recent_workflows"]] == ["Account A Analyst"]
    assert "Account B Private Role" not in account_a.text

    auth_state["user"] = owner_b
    account_b = client.get("/api/dashboard/overview")
    assert account_b.status_code == 200
    assert account_b.json()["summary"]["total_workflows"] == 1
    assert [item["job_title"] for item in account_b.json()["recent_workflows"]] == ["Account B Private Role"]
    assert "Account A Analyst" not in account_b.text


def test_overview_rejects_unauthenticated_access(dashboard_client):
    client, _engine, _owner_a, _owner_b, _auth_state = dashboard_client
    main.app.dependency_overrides.pop(get_auth_context)
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 401


def test_workflow_without_optional_review_is_reported_as_pending(dashboard_client):
    client, engine, owner_a, _owner_b, _auth_state = dashboard_client
    with Session(engine) as db:
        add_workflow(
            db, owner_a.id, "workflow-no-review-dashboard", "No Review Analyst",
            "awaiting_human_review", datetime(2026, 10, 1, tzinfo=timezone.utc),
            None, None, None, include_review=False,
        )
        db.commit()

    response = client.get("/api/dashboard/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_workflows"] == 1
    assert body["summary"]["pending_reviews"] == 1
    assert body["review_decision_distribution"] == [{"decision": "pending", "count": 1}]
    assert body["recent_workflows"][0]["review_decision"] == "pending"
    assert [event["event_type"] for event in body["recent_activity"]] == ["workflow_created"]


def test_overview_database_errors_return_a_safe_unavailable_response(dashboard_client, monkeypatch):
    client, _engine, _owner_a, _owner_b, _auth_state = dashboard_client

    def fail(_db, _owner_id):
        raise OperationalError("SELECT", {}, RuntimeError("private database diagnostic"))

    monkeypatch.setattr(main, "get_dashboard_overview", fail)
    response = client.get("/api/dashboard/overview")
    assert response.status_code == 503
    assert response.json() == {"detail": "Dashboard data is temporarily unavailable."}
    assert "private database diagnostic" not in response.text
