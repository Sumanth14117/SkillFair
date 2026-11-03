"""Opt-in integration checks against the dedicated SkillFair test PostgreSQL only.

Run with SKILLFAIR_RUN_POSTGRES_INTEGRATION=1. The test harness loads the
ignored backend/.env.test.local itself and refuses any target other than
127.0.0.1:5440/skillfair_test_db as skillfair_test_app.
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from dotenv import dotenv_values
from docx import Document
from sqlalchemy import delete, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app import database
from app.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from app.career_orchestrator import CareerWorkflowReviewError
from app.db_models import CareerWorkflow, ImpactObservation, RecruiterFeedback, ResumeAnalysis, User, WorkflowReview
from app.impact_persistence import submit_baseline_observation, submit_recruiter_feedback
from app.models import (
    CareerHumanReviewRequest,
    ImpactBaselineObservationRequest,
    RecruiterImpactFeedbackRequest,
)
from app.persistence import apply_workflow_review


pytestmark = pytest.mark.usefixtures("postgres_test_target")

_INTEGRATION_FLAG = "SKILLFAIR_RUN_POSTGRES_INTEGRATION"
_ORIGIN = "http://localhost:5174"
_PASSWORD_PREFIX = "PG-integration-only-"
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_TEST_CONFIG = _BACKEND_DIR / ".env.test.local"


@pytest.fixture(scope="session")
def postgres_test_target() -> dict[str, str]:
    if os.getenv(_INTEGRATION_FLAG) != "1":
        pytest.skip(f"Set {_INTEGRATION_FLAG}=1 to run the isolated PostgreSQL integration suite.")
    values = dotenv_values(_TEST_CONFIG)
    required = ("DATABASE_URL", "SKILLFAIR_MIGRATION_DATABASE_URL")
    if any(not values.get(key) for key in required):
        raise pytest.UsageError("PostgreSQL integration config is missing a required test URL.")

    expected = {
        "DATABASE_URL": ("127.0.0.1", 5440, "skillfair_test_db", "skillfair_test_app"),
        "SKILLFAIR_MIGRATION_DATABASE_URL": (
            "127.0.0.1", 5440, "skillfair_test_db", "skillfair_test_migrator"
        ),
    }
    for key, target in expected.items():
        try:
            parsed = make_url(values[key])
            actual = (parsed.host, parsed.port, parsed.database, parsed.username)
        except Exception as exc:
            raise pytest.UsageError(f"{key} is invalid for the PostgreSQL integration suite.") from exc
        if actual != target or not parsed.password:
            raise pytest.UsageError(f"{key} does not target the approved SkillFair test database and role.")

    # Connect only with the runtime URL, and verify the server confirms the
    # expected database and role before any test creates records.
    engine = database.create_database_engine(values["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            current_database, current_user = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
            if (current_database, current_user) != ("skillfair_test_db", "skillfair_test_app"):
                raise pytest.UsageError("PostgreSQL server identity did not match the approved test target.")
    except pytest.UsageError:
        raise
    except Exception as exc:
        # Driver exceptions may embed the complete connection URL. Do not chain
        # them into pytest output, which could expose local test credentials.
        raise pytest.UsageError("The approved PostgreSQL test target is not reachable with its runtime role.") from None
    finally:
        engine.dispose()
    return {
        "database_url": values["DATABASE_URL"],
        "migration_url": values["SKILLFAIR_MIGRATION_DATABASE_URL"],
    }


class _OwnedBackend:
    """Own and restart only the Uvicorn child started by this fixture."""

    def __init__(self, target: dict[str, str]) -> None:
        self.target = target
        self.port = self._free_loopback_port()
        self.process: subprocess.Popen[bytes] | None = None
        self.base_url = f"http://127.0.0.1:{self.port}"

    @staticmethod
    def _free_loopback_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("The integration backend process is already running.")
        env = os.environ.copy()
        env.update({
            "DATABASE_URL": self.target["database_url"],
            "SKILLFAIR_MIGRATION_DATABASE_URL": self.target["migration_url"],
            "SKILLFAIR_LLM_PROVIDER": "none",
            "SKILLFAIR_AUTH_ALLOWED_ORIGINS": f"{_ORIGIN},http://127.0.0.1:5174",
            "SKILLFAIR_AUTH_COOKIE_SECURE": "false",
            "SKILLFAIR_AUTH_RATE_LIMIT_PER_MINUTE": "1000",
            "SKILLFAIR_DISCOVERY_RATE_LIMIT_PER_MINUTE": "1000",
            "SKILLFAIR_ORCHESTRATOR_RATE_LIMIT_PER_MINUTE": "1000",
            "SKILLFAIR_RATE_LIMIT_PER_MINUTE": "1000",
            "PGAPPNAME": "skillfair-postgres-integration-test",
        })
        for secret_name in (
            "SKILLFAIR_GEMINI_API_KEY", "SKILLFAIR_GROQ_API_KEY",
            "GEMINI_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
        ):
            env.pop(secret_name, None)
        self.process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(self.port), "--no-access-log",
            ],
            cwd=_BACKEND_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("The test-owned backend exited before its health check passed.")
            try:
                response = httpx.get(f"{self.base_url}/api/health/database", timeout=1, trust_env=False)
                if response.status_code == 200 and response.json() == {
                    "status": "reachable", "configured": True, "reachable": True
                }:
                    return
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.1)
        self.stop()
        raise RuntimeError("The test-owned backend did not become healthy against the approved test database.")

    def stop(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # This is the exact Popen child created above, never an existing service.
            process.kill()
            process.wait(timeout=5)

    def restart(self) -> None:
        self.stop()
        self.start()


@pytest.fixture(scope="session")
def postgres_backend(postgres_test_target: dict[str, str]):
    backend = _OwnedBackend(postgres_test_target)
    backend.start()
    try:
        yield backend
    finally:
        backend.stop()


@dataclass
class _TestCase:
    backend: _OwnedBackend
    engine: object
    run_tag: str = field(default_factory=lambda: uuid4().hex)
    created_emails: list[str] = field(default_factory=list)
    clients: list[httpx.Client] = field(default_factory=list)

    def client(self) -> httpx.Client:
        client = httpx.Client(base_url=self.backend.base_url, timeout=20, trust_env=False)
        self.clients.append(client)
        return client

    def account(self, label: str) -> tuple[httpx.Client, str, str]:
        email = f"pgint-{self.run_tag}-{label}@skillfair.test"
        with Session(self.engine) as session:  # type: ignore[arg-type]
            exists = session.scalar(select(User.id).where(User.email_normalized == email))
        if exists is not None:
            raise RuntimeError("A generated integration-test email already exists; refusing to reuse it.")
        # This exact address was verified absent immediately before this test
        # created it, so teardown can safely delete only its own account graph.
        self.created_emails.append(email)
        password = _PASSWORD_PREFIX + uuid4().hex
        client = self.client()
        response = client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
            headers={"Origin": _ORIGIN},
        )
        assert response.status_code == 202
        login = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers={"Origin": _ORIGIN},
        )
        assert login.status_code == 200
        assert client.cookies.get(SESSION_COOKIE_NAME)
        return client, email, password

    def additional_session(self, email: str, password: str, existing: httpx.Client) -> httpx.Client:
        """Log in independently as the same account, creating a distinct DB session."""
        client = self.client()
        response = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers={"Origin": _ORIGIN},
        )
        assert response.status_code == 200
        assert client.cookies.get(SESSION_COOKIE_NAME)
        assert client.cookies.get(CSRF_COOKIE_NAME)
        assert client.cookies.get(SESSION_COOKIE_NAME) != existing.cookies.get(SESSION_COOKIE_NAME)
        assert client.get("/api/auth/me").json()["id"] == existing.get("/api/auth/me").json()["id"]
        return client

    @staticmethod
    def csrf_headers(client: httpx.Client) -> dict[str, str]:
        csrf = client.cookies.get(CSRF_COOKIE_NAME)
        assert csrf
        return {"Origin": _ORIGIN, "X-CSRF-Token": csrf}

    def analysis(self, client: httpx.Client) -> dict:
        document = Document()
        document.add_heading("Skills", level=1)
        document.add_paragraph("Python, SQL")
        buffer = io.BytesIO()
        document.save(buffer)
        response = client.post(
            "/api/skills/discover",
            headers=self.csrf_headers(client),
            data={"analysis_mode": "local"},
            files={
                "file": (
                    "integration-resume.docx",
                    buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["analysis_id"]
        assert {item["skill_name"] for item in body["skills"]} >= {"Python", "SQL"}
        assert all(item["supporting_excerpt"] for item in body["skills"])
        return body

    def workflow(self, client: httpx.Client, analysis_id: str) -> dict:
        response = client.post(
            "/api/career/orchestrator/runs",
            headers=self.csrf_headers(client),
            json={
                "analysis_id": analysis_id,
                "job_title": "PostgreSQL Integration Analyst",
                "job_description": "Use Python and SQL to prepare reliable operational reporting.",
                "geography": "Test environment",
                "essential_skills": ["Python"],
                "preferred_skills": ["SQL"],
            },
        )
        assert response.status_code == 200
        return response.json()

    def close(self) -> None:
        for client in self.clients:
            client.close()
        self.clients.clear()
        if self.created_emails:
            with Session(self.engine) as session:  # type: ignore[arg-type]
                session.execute(delete(User).where(User.email_normalized.in_(self.created_emails)))
                session.commit()
                remaining = session.scalars(
                    select(User.email_normalized).where(User.email_normalized.in_(self.created_emails))
                ).all()
                if remaining:
                    raise RuntimeError("Test-owned account cleanup did not complete; records were retained.")
        self.engine.dispose()  # type: ignore[attr-defined]


@pytest.fixture
def pg_case(postgres_backend: _OwnedBackend, postgres_test_target: dict[str, str]):
    engine = database.create_database_engine(postgres_test_target["database_url"])
    case = _TestCase(backend=postgres_backend, engine=engine)
    try:
        yield case
    finally:
        case.close()


def _review_payload(workflow: dict, decision: str) -> dict:
    return {
        "decision": decision,
        "reviewed_requirement_ids": workflow["human_review"]["reviewable_requirement_ids"],
    }


def _wait_for_postgres_lock_waiters(case: _TestCase, query_pattern: str, expected: int = 2) -> None:
    """Wait for live test-backend sessions to block at the intended SQL statement."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        with Session(case.engine) as session:  # type: ignore[arg-type]
            waiting = session.scalar(text("""
                SELECT count(*)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND usename = current_user
                  AND pid <> pg_backend_pid()
                  AND application_name = 'skillfair-postgres-integration-test'
                  AND state = 'active'
                  AND wait_event_type = 'Lock'
                  AND query ILIKE :query_pattern
            """), {"query_pattern": query_pattern})
        if int(waiting or 0) >= expected:
            return
        time.sleep(0.025)
    raise AssertionError(
        f"Expected {expected} PostgreSQL sessions waiting on the target operation {query_pattern!r}."
    )


def _concurrent_reviews(
    case: _TestCase,
    clients: tuple[httpx.Client, httpx.Client],
    workflow: dict,
    decisions: tuple[str, str],
):
    """Hold the workflow row so both independently-authenticated requests reach its FOR UPDATE."""
    run_id = workflow["run_id"]
    holder = Session(case.engine)  # type: ignore[arg-type]
    locked_id = holder.scalar(
        select(CareerWorkflow.id).where(CareerWorkflow.public_id == run_id).with_for_update()
    )
    assert locked_id is not None
    barrier = threading.Barrier(3)

    def submit(client: httpx.Client, decision: str) -> tuple[int, dict]:
        csrf = client.cookies.get(CSRF_COOKIE_NAME)
        assert csrf and client.cookies.get(SESSION_COOKIE_NAME)
        barrier.wait(timeout=15)
        result = client.post(
            f"/api/career/orchestrator/runs/{run_id}/review",
            headers={"Origin": _ORIGIN, "X-CSRF-Token": csrf},
            json=_review_payload(workflow, decision),
        )
        return result.status_code, result.json()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(submit, client, decision)
                for client, decision in zip(clients, decisions, strict=True)
            ]
            barrier.wait(timeout=15)
            # Authentication commits each distinct user_sessions row first.
            # This confirms both requests then reached the workflow row lock.
            try:
                _wait_for_postgres_lock_waiters(case, "%career_workflows%FOR UPDATE%")
            except BaseException:
                holder.rollback()
                raise
            holder.rollback()
            return [future.result(timeout=40) for future in futures]
    finally:
        holder.rollback()
        holder.close()


def _assert_one_committed_decision(case: _TestCase, client: httpx.Client, workflow: dict, responses: list[tuple[int, dict]]) -> str:
    success = [body for status, body in responses if status == 200]
    conflicts = [body for status, body in responses if status == 409]
    assert len(success) == 1
    assert len(conflicts) == 1
    run_id = workflow["run_id"]
    restored = client.get(f"/api/career/orchestrator/runs/{run_id}")
    assert restored.status_code == 200
    final = restored.json()
    winner = final["human_review"]["decision"]
    assert success[0] == final
    assert winner in {"proceed", "revise", "stop"}
    with Session(case.engine) as session:  # type: ignore[arg-type]
        row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == run_id))
        assert row is not None and row.version == 2
        reviews = session.scalars(select(WorkflowReview).where(WorkflowReview.workflow_id == row.id)).all()
        assert len(reviews) == 1 and reviews[0].decision == winner
        assert reviews[0].reviewed_requirement_ids == workflow["human_review"]["reviewable_requirement_ids"]
    if winner == "proceed":
        assert final["workflow_status"] == "complete"
        assert final["learning_pathways"]["status"] == "complete"
        assert final["learning_pathways"]["result"] is not None
    else:
        assert final["learning_pathways"]["result"] is None
    return winner


def test_postgres_account_isolation_and_linked_analysis_ownership(pg_case: _TestCase):
    case = pg_case
    client_a, _, _ = case.account("owner-a")
    client_b, _, _ = case.account("owner-b")
    analysis_a = case.analysis(client_a)
    analysis_b = case.analysis(client_b)
    workflow_a = case.workflow(client_a, analysis_a["analysis_id"])
    workflow_b = case.workflow(client_b, analysis_b["analysis_id"])

    assert client_a.get("/api/skills/analyses/latest").json()["analysis_id"] == analysis_a["analysis_id"]
    assert client_b.get("/api/skills/analyses/latest").json()["analysis_id"] == analysis_b["analysis_id"]
    assert client_a.get(f"/api/career/orchestrator/runs/{workflow_a['run_id']}").status_code == 200
    assert client_b.get(f"/api/career/orchestrator/runs/{workflow_b['run_id']}").status_code == 200
    assert client_a.get(f"/api/career/orchestrator/runs/{workflow_b['run_id']}").status_code == 404
    assert client_b.get(f"/api/career/orchestrator/runs/{workflow_a['run_id']}").status_code == 404

    foreign_match = client_a.post("/api/jobs/match", json={
        "analysis_id": analysis_b["analysis_id"],
        "job_title": "Analyst",
        "job_description": "Use Python.",
        "required_skills": ["Python"],
    })
    assert foreign_match.status_code == 404
    foreign_workflow = client_a.post(
        "/api/career/orchestrator/runs",
        headers=case.csrf_headers(client_a),
        json={
            "analysis_id": analysis_b["analysis_id"],
            "job_title": "Analyst",
            "job_description": "Use Python.",
            "essential_skills": ["Python"],
            "preferred_skills": [],
        },
    )
    assert foreign_workflow.status_code == 404
    foreign_review = client_a.post(
        f"/api/career/orchestrator/runs/{workflow_b['run_id']}/review",
        headers=case.csrf_headers(client_a),
        json=_review_payload(workflow_b, "stop"),
    )
    assert foreign_review.status_code == 404

    with Session(case.engine) as session:  # type: ignore[arg-type]
        owner_a = session.scalar(select(User).where(User.email_normalized.like(f"pgint-{case.run_tag}-owner-a@%")))
        owner_b_analysis = session.scalar(select(ResumeAnalysis).where(
            ResumeAnalysis.public_id == analysis_b["analysis_id"]
        ))
        assert owner_a is not None and owner_b_analysis is not None
        # The database composite ownership FK independently rejects a workflow
        # that pairs one owner's ID with another owner's analysis.
        bad = CareerWorkflow(
            public_id=f"pgint-invalid-{uuid4().hex}",
            owner_user_id=owner_a.id,
            analysis_id=owner_b_analysis.id,
            job_title="Invalid cross-owner fixture",
            job_description="Must fail the composite ownership constraint.",
            essential_skills=["Python"],
            preferred_skills=[],
            workflow_status="awaiting_human_review",
            reviewable_requirement_ids=["req-001"],
            stages_snapshot=[],
            skill_passport_snapshot={},
            market_intelligence_snapshot={},
            job_audit_snapshot={},
            inclusive_matching_snapshot={},
            learning_pathways_snapshot={},
            workflow_summary="Invalid fixture",
            version=1,
        )
        from sqlalchemy.exc import IntegrityError

        session.add(bad)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


@pytest.mark.parametrize("decisions", [("proceed", "revise"), ("proceed", "stop")])
def test_postgres_concurrent_conflicting_reviews_commit_exactly_one(pg_case: _TestCase, decisions: tuple[str, str]):
    case = pg_case
    client, email, password = case.account("conflict")
    second = case.additional_session(email, password, client)
    analysis = case.analysis(client)
    workflow = case.workflow(client, analysis["analysis_id"])
    outcomes = _concurrent_reviews(case, (client, second), workflow, decisions)
    _assert_one_committed_decision(case, client, workflow, outcomes)


def test_postgres_concurrent_duplicate_proceed_is_idempotent(pg_case: _TestCase):
    case = pg_case
    client, email, password = case.account("duplicate")
    second = case.additional_session(email, password, client)
    analysis = case.analysis(client)
    workflow = case.workflow(client, analysis["analysis_id"])
    responses = _concurrent_reviews(case, (client, second), workflow, ("proceed", "proceed"))
    assert [status for status, _ in responses] == [200, 200]
    assert responses[0][1] == responses[1][1]
    winner = _assert_one_idempotent_state(case, client, workflow, responses)
    assert winner["human_review"]["decision"] == "proceed"


def _assert_one_idempotent_state(case: _TestCase, client: httpx.Client, workflow: dict, responses: list[tuple[int, dict]]) -> dict:
    run_id = workflow["run_id"]
    final_response = client.get(f"/api/career/orchestrator/runs/{run_id}")
    assert final_response.status_code == 200
    final = final_response.json()
    assert responses[0][1] == final == responses[1][1]
    assert final["workflow_status"] == "complete"
    assert final["learning_pathways"]["status"] == "complete"
    assert final["learning_pathways"]["result"] is not None
    with Session(case.engine) as session:  # type: ignore[arg-type]
        row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == run_id))
        assert row is not None and row.version == 2
        assert len(session.scalars(select(WorkflowReview).where(WorkflowReview.workflow_id == row.id)).all()) == 1
    return final


@pytest.mark.parametrize(
    ("decision", "expected_status"),
    [("proceed", "complete"), ("revise", "revise_required"), ("stop", "stopped")],
)
def test_postgres_atomic_decisions_and_learning_pathways(pg_case: _TestCase, decision: str, expected_status: str):
    case = pg_case
    client, _, _ = case.account(f"atomic-{decision}")
    analysis = case.analysis(client)
    workflow = case.workflow(client, analysis["analysis_id"])
    run_id = workflow["run_id"]
    path = f"/api/career/orchestrator/runs/{run_id}/review"

    invalid = client.post(path, headers=case.csrf_headers(client), json={
        "decision": decision,
        "reviewed_requirement_ids": ["unknown-requirement"],
    })
    assert invalid.status_code == 409
    still_pending = client.get(f"/api/career/orchestrator/runs/{run_id}").json()
    assert still_pending["human_review"]["decision"] is None
    assert still_pending["learning_pathways"]["result"] is None

    response = client.post(path, headers=case.csrf_headers(client), json=_review_payload(workflow, decision))
    assert response.status_code == 200
    body = response.json()
    assert body["workflow_status"] == expected_status
    if decision == "proceed":
        assert body["human_review"]["decision"] == "proceed"
        assert body["learning_pathways"]["status"] == "complete"
        assert body["learning_pathways"]["result"] is not None
    else:
        assert body["learning_pathways"]["result"] is None
        assert body["learning_pathways"]["status"] in {"blocked_pending_review", "stopped"}

    with Session(case.engine) as session:  # type: ignore[arg-type]
        row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == run_id))
        assert row is not None and row.version == 2
        review = session.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == row.id))
        assert review is not None and review.decision == decision
        assert session.scalar(select(WorkflowReview.id).where(WorkflowReview.workflow_id == row.id)) == review.id


def test_postgres_database_failure_rolls_back_review_and_pathways(pg_case: _TestCase):
    case = pg_case
    client, _, _ = case.account("rollback")
    analysis = case.analysis(client)
    workflow = case.workflow(client, analysis["analysis_id"])
    user_id = UUID(client.get("/api/auth/me").json()["id"])
    request = CareerHumanReviewRequest.model_validate(_review_payload(workflow, "proceed"))
    max_postgres_integer = 2_147_483_647

    with Session(case.engine) as session:  # type: ignore[arg-type]
        row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == workflow["run_id"]))
        assert row is not None and row.version == 1
        # This remains valid under the existing version >= 1 check constraint.
        # The normal transition's version increment then exceeds PostgreSQL's
        # INTEGER range and raises SQLSTATE 22003 during its database flush.
        row.version = max_postgres_integer
        session.commit()

    with Session(case.engine) as session:  # type: ignore[arg-type]
        try:
            with pytest.raises(DataError) as failure:
                apply_workflow_review(session, user_id, workflow["run_id"], request, user_id)
            assert getattr(failure.value.orig, "sqlstate", None) == "22003"
        finally:
            session.rollback()
        # The connection that encountered the database error remains usable
        # after rollback, rather than being left in PostgreSQL's aborted state.
        assert session.scalar(text("SELECT 1")) == 1

    # Exercise the actual HTTP route too: the dependency rolls its transaction
    # back, and the centralized handler returns only its generic safe response.
    response = client.post(
        f"/api/career/orchestrator/runs/{workflow['run_id']}/review",
        headers=case.csrf_headers(client),
        json=_review_payload(workflow, "proceed"),
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "An unexpected server error occurred. Please try again."}

    persisted = client.get(f"/api/career/orchestrator/runs/{workflow['run_id']}")
    assert persisted.status_code == 200
    body = persisted.json()
    assert body["human_review"]["decision"] is None
    assert body["workflow_status"] == "awaiting_human_review"
    assert body["learning_pathways"]["status"] == "blocked_pending_review"
    assert body["learning_pathways"]["result"] is None
    with Session(case.engine) as session:  # type: ignore[arg-type]
        row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == workflow["run_id"]))
        review = session.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == row.id))
        assert len(session.scalars(select(CareerWorkflow.id).where(
            CareerWorkflow.owner_user_id == user_id
        )).all()) == 1
        assert len(session.scalars(select(WorkflowReview.id).where(
            WorkflowReview.workflow_id == row.id
        )).all()) == 1
        assert row.version == max_postgres_integer
        assert review.decision is None
        assert review.review_completed_at is None
        assert row.learning_pathways_snapshot["status"] == "blocked_pending_review"
        assert row.learning_pathways_snapshot["result"] is None
        assert session.scalar(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == user_id
        )) is None
        assert session.scalar(select(RecruiterFeedback.id).where(
            RecruiterFeedback.owner_user_id == user_id
        )) is None
        assert session.scalar(text("SELECT 1")) == 1


def test_postgres_backend_restart_restores_analysis_workflow_review_and_login(pg_case: _TestCase):
    case = pg_case
    client, email, password = case.account("restart")
    analysis = case.analysis(client)
    workflow = case.workflow(client, analysis["analysis_id"])
    proceed = client.post(
        f"/api/career/orchestrator/runs/{workflow['run_id']}/review",
        headers=case.csrf_headers(client),
        json=_review_payload(workflow, "proceed"),
    )
    assert proceed.status_code == 200
    assert proceed.json()["learning_pathways"]["status"] == "complete"
    client.close()
    case.clients.remove(client)

    case.backend.restart()

    restored_client = case.client()
    login = restored_client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": _ORIGIN},
    )
    assert login.status_code == 200
    assert restored_client.get("/api/auth/me").status_code == 200

    restored_analysis = restored_client.get("/api/skills/analyses/latest")
    assert restored_analysis.status_code == 200
    analysis_body = restored_analysis.json()
    assert analysis_body["analysis_id"] == analysis["analysis_id"]
    python = next(item for item in analysis_body["skills"] if item["skill_name"] == "Python")
    assert python["supporting_excerpt"] == "Python, SQL"

    restored_workflow = restored_client.get(f"/api/career/orchestrator/runs/{workflow['run_id']}")
    assert restored_workflow.status_code == 200
    workflow_body = restored_workflow.json()
    assert workflow_body["human_review"]["decision"] == "proceed"
    assert workflow_body["human_review"]["status"] == "recorded"
    assert workflow_body["learning_pathways"]["status"] == "complete"
    assert workflow_body["learning_pathways"]["result"] is not None


def test_postgres_impact_is_account_scoped_persistent_and_uses_committed_workflow_data(pg_case: _TestCase):
    case = pg_case
    recruiter, _, _ = case.account("impact-owner")
    other, _, _ = case.account("impact-other")
    analysis = case.analysis(recruiter)
    workflows = [case.workflow(recruiter, analysis["analysis_id"]) for _ in range(5)]
    decisions = ("proceed", "revise", "stop", "revise", "stop")
    for workflow, decision in zip(workflows, decisions, strict=True):
        started = recruiter.post(
            f"/api/impact/reviews/{workflow['run_id']}/start",
            headers=case.csrf_headers(recruiter),
        )
        assert started.status_code == 200
        assert started.json()["duplicate"] is False
        assert recruiter.post(
            f"/api/impact/reviews/{workflow['run_id']}/start",
            headers=case.csrf_headers(recruiter),
        ).json()["duplicate"] is True
        result = recruiter.post(
            f"/api/career/orchestrator/runs/{workflow['run_id']}/review",
            headers=case.csrf_headers(recruiter),
            json=_review_payload(workflow, decision),
        )
        assert result.status_code == 200
        body = result.json()
        if decision == "proceed":
            assert body["learning_pathways"]["status"] == "complete"
            assert body["learning_pathways"]["result"] is not None
        else:
            assert body["learning_pathways"]["result"] is None

    for index in range(5):
        feedback = recruiter.post("/api/impact/feedback/recruiter", headers=case.csrf_headers(recruiter), json={
            "submission_id": str(uuid4()), "consent_to_aggregate": True,
            "evidence_usefulness": 5, "explanation_clarity": 4, "review_ease": 3,
        })
        assert feedback.status_code == 200
        baseline = recruiter.post("/api/impact/baseline-observations", headers=case.csrf_headers(recruiter), json={
            "submission_id": str(uuid4()), "consent_to_aggregate": True,
            "review_duration_seconds": 120 + index,
        })
        assert baseline.status_code == 200

    linked_to_other = recruiter.post("/api/impact/feedback/recruiter", headers=case.csrf_headers(recruiter), json={
        "submission_id": str(uuid4()), "workflow_id": case.workflow(other, case.analysis(other)["analysis_id"])["run_id"],
        "consent_to_aggregate": True, "evidence_usefulness": 4,
        "explanation_clarity": 4, "review_ease": 4,
    })
    assert linked_to_other.status_code == 404

    summary_response = recruiter.get("/api/impact/summary?category=all")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    metric_map = {item["metric_id"]: item for item in summary["metrics"]}
    assert metric_map["completed_reviews"]["sample_size"] == 5
    assert metric_map["completed_reviews"]["value"] == 5
    assert metric_map["incomplete_reviews"]["value"] == 0
    assert metric_map["learning_pathways_generated"]["value"] == 1
    assert metric_map["recruiter_evidence_usefulness"]["value"] == 5
    assert {item["label"]: item["value"] for item in summary["decision_distribution"]} == {
        "proceed": 1, "revise": 2, "stop": 2,
    }
    assert summary["baseline_comparison"]["data_availability"] == "measured"
    assert summary["baseline_comparison"]["baseline_sample_size"] == 5
    assert summary["candidate_rating_distribution"][0]["data_availability"] == "unavailable"
    assert not any(key in str(summary) for key in (str(UUID(recruiter.get("/api/auth/me").json()["id"])), workflows[0]["run_id"]))

    other_summary = other.get("/api/impact/summary?category=all").json()
    other_metrics = {item["metric_id"]: item for item in other_summary["metrics"]}
    assert other_metrics["completed_reviews"]["sample_size"] == 0
    assert other_metrics["recruiter_evidence_usefulness"]["sample_size"] == 0

    # Use the dedicated restart fixture path to verify server restart without
    # changing the normal application or touching any other process.
    restart_client = case.client()
    csrf = recruiter.cookies.get(CSRF_COOKIE_NAME)
    assert csrf
    cookies = dict(recruiter.cookies)
    restart_client.cookies.update(cookies)
    recruiter.close()
    case.clients.remove(recruiter)
    case.backend.restart()
    # The persisted signed-in session remains valid across an application restart.
    restored = restart_client.get("/api/impact/summary?category=all")
    assert restored.status_code == 200
    assert {item["metric_id"]: item for item in restored.json()["metrics"]}["completed_reviews"]["value"] == 5

    with Session(case.engine) as session:  # type: ignore[arg-type]
        assert session.scalar(select(RecruiterFeedback.id).where(RecruiterFeedback.owner_user_id == UUID(other.get("/api/auth/me").json()["id"]))) is None
        assert session.scalar(select(ImpactObservation.id).where(ImpactObservation.owner_user_id == UUID(other.get("/api/auth/me").json()["id"]))) is None


def test_postgres_concurrent_duplicate_recruiter_submission_is_idempotent(pg_case: _TestCase):
    case = pg_case
    client, email, password = case.account("impact-concurrent")
    second = case.additional_session(email, password, client)
    owner_id = UUID(client.get("/api/auth/me").json()["id"])
    payload = {
        "submission_id": str(uuid4()), "consent_to_aggregate": True,
        "evidence_usefulness": 5, "explanation_clarity": 4, "review_ease": 3,
    }
    feedback_id = UUID(payload["submission_id"])
    feedback_seed = RecruiterFeedback(
        owner_user_id=owner_id,
        workflow_id=None,
        submission_id=feedback_id,
        consent_to_aggregate=True,
        evidence_usefulness=payload["evidence_usefulness"],
        explanation_clarity=payload["explanation_clarity"],
        review_ease=payload["review_ease"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
    )

    def submit_feedback(target: httpx.Client) -> httpx.Response:
        return target.post("/api/impact/feedback/recruiter", headers=case.csrf_headers(target), json=payload)

    responses = _concurrent_duplicate_submission(
        case, (client, second), feedback_seed, "%INSERT INTO recruiter_feedback%", submit_feedback
    )
    assert all(response.status_code == 200 for response in responses)
    assert sorted(response.json()["duplicate"] for response in responses) == [False, True]
    with Session(case.engine) as session:  # type: ignore[arg-type]
        assert len(session.scalars(select(RecruiterFeedback.id).where(
            RecruiterFeedback.owner_user_id == owner_id,
            RecruiterFeedback.submission_id == feedback_id,
        )).all()) == 1
    summary = client.get("/api/impact/summary?category=recruiter_feedback").json()
    assert {item["metric_id"]: item for item in summary["metrics"]}["recruiter_evidence_usefulness"]["sample_size"] == 1

    baseline_payload = {
        "submission_id": str(uuid4()), "consent_to_aggregate": True,
        "review_duration_seconds": 180,
    }
    baseline_id = UUID(baseline_payload["submission_id"])
    baseline_seed = ImpactObservation(
        owner_user_id=owner_id,
        workflow_review_id=None,
        submission_id=baseline_id,
        observation_type="baseline_duration",
        numeric_value=baseline_payload["review_duration_seconds"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
    )

    def submit_baseline(target: httpx.Client) -> httpx.Response:
        return target.post("/api/impact/baseline-observations", headers=case.csrf_headers(target), json=baseline_payload)

    baseline_responses = _concurrent_duplicate_submission(
        case, (client, second), baseline_seed, "%INSERT INTO impact_observations%", submit_baseline
    )
    assert all(response.status_code == 200 for response in baseline_responses)
    assert sorted(response.json()["duplicate"] for response in baseline_responses) == [False, True]
    with Session(case.engine) as session:  # type: ignore[arg-type]
        assert len(session.scalars(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == owner_id,
            ImpactObservation.submission_id == baseline_id,
        )).all()) == 1
    baseline_summary = client.get("/api/impact/summary?category=baseline").json()
    assert baseline_summary["baseline_comparison"]["baseline_sample_size"] == 1
    second.close()


# ---------------------------------------------------------------------------
# M4.4 — complete Impact Measurement integration coverage.
# ---------------------------------------------------------------------------


def _summary(client: httpx.Client, category: str = "all") -> dict:
    response = client.get(f"/api/impact/summary?category={category}")
    assert response.status_code == 200, response.text
    return response.json()


def _metrics(summary: dict) -> dict[str, dict]:
    return {item["metric_id"]: item for item in summary["metrics"]}


def _post_feedback(
    case: _TestCase,
    client: httpx.Client,
    *,
    submission_id: str | None = None,
    workflow_id: str | None = None,
    consent: bool = True,
    evidence: int = 5,
    clarity: int = 4,
    ease: int = 3,
) -> httpx.Response:
    payload = {
        "submission_id": submission_id or str(uuid4()),
        "consent_to_aggregate": consent,
        "evidence_usefulness": evidence,
        "explanation_clarity": clarity,
        "review_ease": ease,
    }
    if workflow_id is not None:
        payload["workflow_id"] = workflow_id
    return client.post("/api/impact/feedback/recruiter", headers=case.csrf_headers(client), json=payload)


def _post_baseline(
    case: _TestCase,
    client: httpx.Client,
    *,
    submission_id: str | None = None,
    seconds: int = 120,
    consent: bool = True,
) -> httpx.Response:
    return client.post("/api/impact/baseline-observations", headers=case.csrf_headers(client), json={
        "submission_id": submission_id or str(uuid4()),
        "consent_to_aggregate": consent,
        "review_duration_seconds": seconds,
    })


def _concurrent_duplicate_submission(
    case: _TestCase,
    clients: tuple[httpx.Client, httpx.Client],
    seed_row: RecruiterFeedback | ImpactObservation,
    query_pattern: str,
    submit,
) -> tuple[httpx.Response, httpx.Response]:
    """Force both API inserts to contend on one uncommitted unique-index key."""
    first_cookie = clients[0].cookies.get(SESSION_COOKIE_NAME)
    second_cookie = clients[1].cookies.get(SESSION_COOKIE_NAME)
    assert first_cookie and second_cookie and first_cookie != second_cookie
    holder = Session(case.engine)  # type: ignore[arg-type]
    holder.add(seed_row)
    holder.flush()  # The uncommitted row reserves the unique key until rollback.
    barrier = threading.Barrier(3)

    def send(client: httpx.Client) -> httpx.Response:
        barrier.wait(timeout=15)
        return submit(client)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, client) for client in clients]
            barrier.wait(timeout=15)
            # Both requests pass their owner-scoped existence check and block
            # inside PostgreSQL's INSERT/unique-index arbitration.
            try:
                _wait_for_postgres_lock_waiters(case, query_pattern)
            except BaseException:
                holder.rollback()
                raise
            holder.rollback()
            return futures[0].result(timeout=30), futures[1].result(timeout=30)
    finally:
        holder.rollback()
        holder.close()


def _start_review(case: _TestCase, client: httpx.Client, workflow: dict) -> None:
    response = client.post(
        f"/api/impact/reviews/{workflow['run_id']}/start",
        headers=case.csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["duplicate"] is False


def _decide(case: _TestCase, client: httpx.Client, workflow: dict, decision: str) -> dict:
    response = client.post(
        f"/api/career/orchestrator/runs/{workflow['run_id']}/review",
        headers=case.csrf_headers(client),
        json=_review_payload(workflow, decision),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _assert_no_identifier_keys(payload: object) -> None:
    forbidden = {"owner_user_id", "user_id", "workflow_id", "submission_id", "analysis_id", "id"}
    if isinstance(payload, dict):
        assert forbidden.isdisjoint(payload.keys()), payload.keys() & forbidden
        for value in payload.values():
            _assert_no_identifier_keys(value)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_identifier_keys(value)


def test_postgres_impact_summaries_are_isolated_and_identifier_free(pg_case: _TestCase):
    case = pg_case
    client_a, email_a, _ = case.account("isolated-a")
    client_b, email_b, _ = case.account("isolated-b")
    analysis_a = case.analysis(client_a)
    workflow_a = case.workflow(client_a, analysis_a["analysis_id"])
    _start_review(case, client_a, workflow_a)
    _decide(case, client_a, workflow_a, "proceed")

    submission_ids = []
    for _ in range(5):
        submission_id = str(uuid4())
        submission_ids.append(submission_id)
        assert _post_feedback(case, client_a, submission_id=submission_id).status_code == 200
    for _ in range(5):
        submission_id = str(uuid4())
        submission_ids.append(submission_id)
        assert _post_baseline(case, client_a, submission_id=submission_id).status_code == 200

    summary_a = _summary(client_a)
    summary_b = _summary(client_b)
    metrics_a = _metrics(summary_a)
    metrics_b = _metrics(summary_b)
    assert metrics_a["completed_reviews"]["sample_size"] == 1
    assert metrics_a["recruiter_evidence_usefulness"]["sample_size"] == 5
    assert summary_a["baseline_comparison"]["baseline_sample_size"] == 5

    # Account B sees none of Account A's records, even in the same process and
    # against the same database.
    assert metrics_b["completed_reviews"]["sample_size"] == 0
    assert metrics_b["recruiter_evidence_usefulness"]["sample_size"] == 0
    assert summary_b["baseline_comparison"]["baseline_sample_size"] == 0
    assert metrics_b["candidate_experience_average"]["data_availability"] == "unavailable"

    # Cross-account workflow references are rejected for feedback creation.
    foreign_reference = _post_feedback(case, client_a, workflow_id=case.workflow(
        client_b, case.analysis(client_b)["analysis_id"]
    )["run_id"])
    assert foreign_reference.status_code == 404

    # Aggregate payloads carry no account, workflow, analysis or submission IDs.
    _assert_no_identifier_keys(summary_a)
    serialized = str(summary_a)
    owner_a_id = client_a.get("/api/auth/me").json()["id"]
    assert owner_a_id not in serialized
    assert email_a not in serialized and email_b not in serialized
    assert workflow_a["run_id"] not in serialized
    assert analysis_a["analysis_id"] not in serialized
    assert all(submission_id not in serialized for submission_id in submission_ids)


def test_postgres_impact_consent_csrf_and_candidate_feedback_rules(pg_case: _TestCase):
    case = pg_case
    client, _, _ = case.account("consent")
    other, _, _ = case.account("consent-other")
    other_workflow = case.workflow(other, case.analysis(other)["analysis_id"])

    assert _post_feedback(case, client, consent=False).status_code == 422
    assert _post_baseline(case, client, consent=False).status_code == 422
    candidate = client.post("/api/impact/feedback/candidate", headers=case.csrf_headers(client), json={
        "submission_id": str(uuid4()),
        "consent_to_aggregate": False,
        "skill_representation_clarity": 4,
        "understanding_of_skills": 4,
        "perceived_transparency": 4,
        "overall_experience": 4,
    })
    assert candidate.status_code == 422

    candidate_consented = client.post("/api/impact/feedback/candidate", headers=case.csrf_headers(client), json={
        "submission_id": str(uuid4()),
        "consent_to_aggregate": True,
        "skill_representation_clarity": 4,
        "understanding_of_skills": 4,
        "perceived_transparency": 4,
        "overall_experience": 4,
    })
    assert candidate_consented.status_code == 410

    # CSRF is required for every Impact write, even with a valid session.
    missing_csrf = client.post("/api/impact/feedback/recruiter", headers={"Origin": _ORIGIN}, json={
        "submission_id": str(uuid4()),
        "consent_to_aggregate": True,
        "evidence_usefulness": 4,
        "explanation_clarity": 4,
        "review_ease": 4,
    })
    assert missing_csrf.status_code == 403

    # Anonymous summary access is rejected.
    assert case.client().get("/api/impact/summary").status_code == 401

    # A recruiter cannot attach feedback to another account's workflow.
    assert _post_feedback(case, client, workflow_id=other_workflow["run_id"]).status_code == 404

    owner_id = UUID(client.get("/api/auth/me").json()["id"])
    with Session(case.engine) as session:  # type: ignore[arg-type]
        from app.db_models import CandidateFeedback

        assert session.scalar(select(CandidateFeedback.id).where(
            CandidateFeedback.owner_user_id == owner_id
        )) is None
        assert session.scalar(select(RecruiterFeedback.id).where(
            RecruiterFeedback.owner_user_id == owner_id
        )) is None
        assert session.scalar(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == owner_id
        )) is None


def test_postgres_impact_baseline_dedup_expiry_and_owner_scoped_retention(pg_case: _TestCase):
    case = pg_case
    owner, _, _ = case.account("retention-owner")
    other, _, _ = case.account("retention-other")

    analysis = case.analysis(owner)
    workflow = case.workflow(owner, analysis["analysis_id"])
    _start_review(case, owner, workflow)
    _decide(case, owner, workflow, "proceed")

    baseline_ids = [str(uuid4()) for _ in range(6)]
    for index, submission_id in enumerate(baseline_ids):
        response = _post_baseline(case, owner, submission_id=submission_id, seconds=100 + index)
        assert response.status_code == 200 and response.json()["duplicate"] is False
    duplicate = _post_baseline(case, owner, submission_id=baseline_ids[0], seconds=100)
    assert duplicate.status_code == 200 and duplicate.json()["duplicate"] is True

    feedback_ids = [str(uuid4()) for _ in range(6)]
    for submission_id in feedback_ids:
        assert _post_feedback(case, owner, submission_id=submission_id).status_code == 200
    duplicate_feedback = _post_feedback(case, owner, submission_id=feedback_ids[0])
    assert duplicate_feedback.status_code == 200 and duplicate_feedback.json()["duplicate"] is True

    other_baseline = _post_baseline(case, other, seconds=240)
    other_feedback = _post_feedback(case, other)
    assert other_baseline.status_code == 200 and other_feedback.status_code == 200

    owner_id = UUID(owner.get("/api/auth/me").json()["id"])
    other_id = UUID(other.get("/api/auth/me").json()["id"])
    expired_at = datetime.now(timezone.utc) - timedelta(days=1)
    with Session(case.engine) as session:  # type: ignore[arg-type]
        owner_last_baseline = session.scalar(select(ImpactObservation).where(
            ImpactObservation.owner_user_id == owner_id,
            ImpactObservation.submission_id == UUID(baseline_ids[-1]),
        ))
        owner_last_feedback = session.scalar(select(RecruiterFeedback).where(
            RecruiterFeedback.owner_user_id == owner_id,
            RecruiterFeedback.submission_id == UUID(feedback_ids[-1]),
        ))
        other_baseline_row = session.scalar(select(ImpactObservation).where(
            ImpactObservation.owner_user_id == other_id,
        ))
        other_feedback_row = session.scalar(select(RecruiterFeedback).where(
            RecruiterFeedback.owner_user_id == other_id,
        ))
        assert owner_last_baseline and owner_last_feedback
        assert other_baseline_row and other_feedback_row
        owner_last_baseline.expires_at = expired_at
        owner_last_feedback.expires_at = expired_at
        other_baseline_row.expires_at = expired_at
        other_feedback_row.expires_at = expired_at
        session.commit()

    # Duplicate IDs did not inflate either record count (6, not 7).
    with Session(case.engine) as session:  # type: ignore[arg-type]
        assert len(session.scalars(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == owner_id
        )).all()) == 6
        assert len(session.scalars(select(RecruiterFeedback.id).where(
            RecruiterFeedback.owner_user_id == owner_id
        )).all()) == 6

    summary = _summary(owner)
    metrics = _metrics(summary)
    # The expired owner row is excluded and pruned; the duplicate never counted.
    assert metrics["recruiter_evidence_usefulness"]["sample_size"] == 5
    assert metrics["recruiter_evidence_usefulness"]["data_availability"] == "measured"
    assert summary["baseline_comparison"]["baseline_sample_size"] == 5
    with Session(case.engine) as session:  # type: ignore[arg-type]
        assert len(session.scalars(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == owner_id
        )).all()) == 5
        assert len(session.scalars(select(RecruiterFeedback.id).where(
            RecruiterFeedback.owner_user_id == owner_id
        )).all()) == 5
        # Cleanup is owner-scoped: the other account's expired rows remain.
        assert session.scalar(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == other_id
        )) is not None
        assert session.scalar(select(RecruiterFeedback.id).where(
            RecruiterFeedback.owner_user_id == other_id
        )) is not None
        # Workflow history is never removed by measurement retention cleanup.
        assert session.scalar(select(CareerWorkflow.id).where(
            CareerWorkflow.owner_user_id == owner_id
        )) is not None
        assert session.scalar(select(WorkflowReview.id).where(
            WorkflowReview.owner_user_id == owner_id
        )) is not None

    # The other account's own summary prunes only its rows and still sees none
    # of the first account's measurements.
    other_summary = _summary(other)
    assert _metrics(other_summary)["recruiter_evidence_usefulness"]["sample_size"] == 0
    with Session(case.engine) as session:  # type: ignore[arg-type]
        assert session.scalar(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == other_id
        )) is None
        assert session.scalar(select(RecruiterFeedback.id).where(
            RecruiterFeedback.owner_user_id == other_id
        )) is None
        assert len(session.scalars(select(ImpactObservation.id).where(
            ImpactObservation.owner_user_id == owner_id
        )).all()) == 5
        assert session.scalar(select(CareerWorkflow.id).where(
            CareerWorkflow.owner_user_id == owner_id
        )) is not None


def test_postgres_review_metrics_bound_durations_and_learning_pathways(pg_case: _TestCase):
    case = pg_case
    client, _, _ = case.account("review-metrics")
    analysis = case.analysis(client)
    workflows = [case.workflow(client, analysis["analysis_id"]) for _ in range(5)]
    decisions = ["proceed", "proceed", "revise", "stop", None]

    completed_bodies = {}
    for workflow, decision in zip(workflows, decisions, strict=True):
        _start_review(case, client, workflow)
        if decision is not None:
            completed_bodies[workflow["run_id"]] = _decide(case, client, workflow, decision)

    for workflow, decision in zip(workflows, decisions, strict=True):
        if decision is None:
            continue
        body = completed_bodies[workflow["run_id"]]
        if decision == "proceed":
            assert body["workflow_status"] == "complete"
            assert body["learning_pathways"]["status"] == "complete"
            assert body["learning_pathways"]["result"] is not None
        else:
            assert body["learning_pathways"]["result"] is None

    # Use fixed UTC timestamps to pin all three boundary cases without relying
    # on request timing: just below and exactly 24 hours are valid; one second
    # above is invalid under the backend's inclusive `<= 24h` rule.
    fixed_completed_at = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    expected_durations = {
        workflows[0]["run_id"]: (24 * 60 * 60) - 1,
        workflows[1]["run_id"]: 24 * 60 * 60,
        workflows[2]["run_id"]: 60,
        workflows[3]["run_id"]: (24 * 60 * 60) + 1,
    }
    with Session(case.engine) as session:  # type: ignore[arg-type]
        for run_id, duration_seconds in expected_durations.items():
            workflow_row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == run_id))
            assert workflow_row is not None
            review = session.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == workflow_row.id))
            assert review is not None and review.review_completed_at is not None
            review.review_completed_at = fixed_completed_at
            review.review_started_at = fixed_completed_at - timedelta(seconds=duration_seconds)
        session.commit()

        for run_id, expected_seconds in expected_durations.items():
            workflow_row = session.scalar(select(CareerWorkflow).where(CareerWorkflow.public_id == run_id))
            assert workflow_row is not None
            review = session.scalar(select(WorkflowReview).where(WorkflowReview.workflow_id == workflow_row.id))
            assert review is not None and review.review_started_at is not None and review.review_completed_at is not None
            actual_seconds = (review.review_completed_at - review.review_started_at).total_seconds()
            assert actual_seconds == expected_seconds

    summary = _summary(client)
    metrics = _metrics(summary)
    assert metrics["completed_reviews"]["sample_size"] == 5
    assert metrics["completed_reviews"]["value"] == 4
    assert metrics["incomplete_reviews"]["value"] == 1
    assert metrics["workflow_completion_rate"]["value"] == 80.0
    assert {item["label"]: item["value"] for item in summary["decision_distribution"]} == {
        "proceed": 2, "revise": 1, "stop": 1,
    }
    assert metrics["review_duration_average"]["sample_size"] == 3
    assert metrics["review_duration_average"]["data_availability"] == "insufficient_sample"
    assert metrics["review_duration_average"]["value"] is None
    # The exact-boundary and below-boundary records are included; the above-
    # boundary record is the sole invalid duration.
    assert any("1 invalid" in limitation for limitation in metrics["review_duration_average"]["limitations"])
    assert metrics["learning_pathways_generated"]["value"] == 2

    # A committed Proceed whose persisted Learning Pathways result is no longer
    # valid stops counting. Also construct the adversarial negative case: a
    # committed Revise with an otherwise completed workflow and valid completed
    # pathway result. It must still not count because the decision is not Proceed.
    with Session(case.engine) as session:  # type: ignore[arg-type]
        workflow_row = session.scalar(select(CareerWorkflow).where(
            CareerWorkflow.public_id == workflows[0]["run_id"]
        ))
        assert workflow_row is not None
        workflow_row.learning_pathways_snapshot = {"status": "blocked_pending_review", "result": None}
        revise_row = session.scalar(select(CareerWorkflow).where(
            CareerWorkflow.public_id == workflows[2]["run_id"]
        ))
        assert revise_row is not None
        revise_row.learning_pathways_snapshot = dict(
            completed_bodies[workflows[1]["run_id"]]["learning_pathways"]
        )
        revise_row.workflow_status = "complete"
        revise_review = session.scalar(select(WorkflowReview).where(
            WorkflowReview.workflow_id == revise_row.id
        ))
        assert revise_review is not None and revise_review.decision == "revise"
        assert revise_row.learning_pathways_snapshot["status"] == "complete"
        assert revise_row.learning_pathways_snapshot["result"] is not None
        session.commit()
    refreshed = _metrics(_summary(client))
    assert refreshed["learning_pathways_generated"]["value"] == 1

    with Session(case.engine) as session:  # type: ignore[arg-type]
        stored = session.scalars(select(WorkflowReview).where(
            WorkflowReview.owner_user_id == UUID(client.get("/api/auth/me").json()["id"])
        )).all()
        assert len(stored) == 5
        assert [row.decision for row in stored].count(None) == 1


def test_postgres_failed_impact_transactions_leave_no_partial_rows(pg_case: _TestCase):
    case = pg_case
    client, _, _ = case.account("impact-atomic")
    owner_id = UUID(client.get("/api/auth/me").json()["id"])

    with Session(case.engine) as session:  # type: ignore[arg-type]
        valid_feedback = RecruiterImpactFeedbackRequest(
            submission_id=uuid4(), consent_to_aggregate=True,
            evidence_usefulness=4, explanation_clarity=4, review_ease=4,
        )
        assert submit_recruiter_feedback(session, owner_id, valid_feedback).duplicate is False
        valid_baseline = ImpactBaselineObservationRequest(
            submission_id=uuid4(), consent_to_aggregate=True, review_duration_seconds=300,
        )
        assert submit_baseline_observation(session, owner_id, valid_baseline).duplicate is False

        def fail_new_measurement(current: Session) -> None:
            if any(isinstance(row, (RecruiterFeedback, ImpactObservation)) for row in current.new):
                raise RuntimeError("injected impact persistence failure")

        failing_feedback = RecruiterImpactFeedbackRequest(
            submission_id=uuid4(), consent_to_aggregate=True,
            evidence_usefulness=2, explanation_clarity=2, review_ease=2,
        )
        event.listen(session, "before_commit", fail_new_measurement)
        try:
            with pytest.raises(RuntimeError, match="injected impact persistence failure"):
                submit_recruiter_feedback(session, owner_id, failing_feedback)
        finally:
            event.remove(session, "before_commit", fail_new_measurement)
            session.rollback()

        failing_baseline = ImpactBaselineObservationRequest(
            submission_id=uuid4(), consent_to_aggregate=True, review_duration_seconds=600,
        )
        event.listen(session, "before_commit", fail_new_measurement)
        try:
            with pytest.raises(RuntimeError, match="injected impact persistence failure"):
                submit_baseline_observation(session, owner_id, failing_baseline)
        finally:
            event.remove(session, "before_commit", fail_new_measurement)
            session.rollback()

        feedback_rows = session.scalars(select(RecruiterFeedback).where(
            RecruiterFeedback.owner_user_id == owner_id
        )).all()
        baseline_rows = session.scalars(select(ImpactObservation).where(
            ImpactObservation.owner_user_id == owner_id
        )).all()
        assert len(feedback_rows) == 1 and feedback_rows[0].submission_id == valid_feedback.submission_id
        assert len(baseline_rows) == 1 and baseline_rows[0].submission_id == valid_baseline.submission_id
        assert session.scalar(select(RecruiterFeedback.id).where(
            RecruiterFeedback.submission_id == failing_feedback.submission_id
        )) is None
        assert session.scalar(select(ImpactObservation.id).where(
            ImpactObservation.submission_id == failing_baseline.submission_id
        )) is None


def test_postgres_impact_restart_preserves_login_and_dashboard_aggregates(pg_case: _TestCase):
    case = pg_case
    client, email, password = case.account("impact-restart")
    analysis = case.analysis(client)
    workflows = [case.workflow(client, analysis["analysis_id"]) for _ in range(5)]
    for workflow in workflows:
        _start_review(case, client, workflow)
        body = _decide(case, client, workflow, "proceed")
        assert body["learning_pathways"]["status"] == "complete"

    for _ in range(5):
        assert _post_feedback(case, client, submission_id=str(uuid4())).status_code == 200
    for _ in range(5):
        assert _post_baseline(case, client, seconds=180).status_code == 200

    before = _metrics(_summary(client))
    assert before["completed_reviews"]["value"] == 5
    assert before["recruiter_evidence_usefulness"]["sample_size"] == 5
    assert before["learning_pathways_generated"]["value"] == 5

    client.close()
    case.clients.remove(client)
    case.backend.restart()

    # Re-authenticate with the account password; the persisted session cookie is
    # intentionally not reused for this check.
    restored = case.client()
    login = restored.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": _ORIGIN},
    )
    assert login.status_code == 200
    assert restored.get("/api/auth/me").status_code == 200

    after_summary = _summary(restored)
    after = _metrics(after_summary)
    assert after["completed_reviews"]["value"] == 5
    assert after["completed_reviews"]["sample_size"] == 5
    assert after["recruiter_evidence_usefulness"]["sample_size"] == 5
    assert after["learning_pathways_generated"]["value"] == 5
    assert after_summary["baseline_comparison"]["baseline_sample_size"] == 5
    assert {item["label"]: item["value"] for item in after_summary["decision_distribution"]} == {
        "proceed": 5, "revise": 0, "stop": 0,
    }

    restored_workflow = restored.get(f"/api/career/orchestrator/runs/{workflows[0]['run_id']}")
    assert restored_workflow.status_code == 200
    assert restored_workflow.json()["human_review"]["decision"] == "proceed"
    assert restored.get("/api/skills/analyses/latest").json()["analysis_id"] == analysis["analysis_id"]
