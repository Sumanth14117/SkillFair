from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from dotenv import load_dotenv
from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi import FastAPI

from app import database
from app.auth import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME, _allowed_origins, router as auth_router
from app.db_models import Base, User, UserSession
from app.main import app as skillfair_app


auth_test_application = FastAPI()
auth_test_application.include_router(auth_router)


ORIGIN = "https://skillfair.test"
EMAIL = "Recruiter@Example.test"
PASSWORD = "correct horse battery staple 42"
VITE_ORIGIN = "http://localhost:5174"


@pytest.fixture
def auth_client(monkeypatch):
    engine = database.create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    def override_db():
        with Session(engine) as session:
            yield session

    original_override = auth_test_application.dependency_overrides.get(database.get_db_session)
    auth_test_application.dependency_overrides[database.get_db_session] = override_db
    monkeypatch.setenv("SKILLFAIR_AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", ORIGIN)
    monkeypatch.setenv("SKILLFAIR_AUTH_RATE_LIMIT_PER_MINUTE", "1000")
    try:
        yield engine
    finally:
        if original_override is None:
            auth_test_application.dependency_overrides.pop(database.get_db_session, None)
        else:
            auth_test_application.dependency_overrides[database.get_db_session] = original_override
        engine.dispose()


def _origin_headers(origin: str = ORIGIN) -> dict[str, str]:
    return {"Origin": origin}


async def _register(client: httpx.AsyncClient, email: str = EMAIL, password: str = PASSWORD):
    return await client.post("/api/auth/register", json={"email": email, "password": password}, headers=_origin_headers())


def _session_for_cookie(engine, raw_token: str) -> UserSession:
    digest = hashlib.sha256(raw_token.encode()).hexdigest()
    with Session(engine) as session:
        return session.scalar(select(UserSession).where(UserSession.token_hash == digest))


def test_authentication_routes_are_registered_on_the_existing_application():
    paths = set(skillfair_app.openapi()["paths"])
    assert {
        "/api/auth/register",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",
    } <= paths


def test_development_origin_defaults_include_exact_vite_ports(monkeypatch):
    monkeypatch.delenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", raising=False)
    assert _allowed_origins() == {
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    }


@pytest.mark.anyio
async def test_registration_accepts_configured_localhost_5174_origin(auth_client, monkeypatch):
    monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", VITE_ORIGIN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        response = await client.post(
            "/api/auth/register",
            json={"email": EMAIL, "password": PASSWORD},
            headers={"Origin": VITE_ORIGIN},
        )
    assert response.status_code == 201


@pytest.mark.anyio
async def test_registration_accepts_configured_127_0_0_1_5174_origin(auth_client, monkeypatch):
    origin = "http://127.0.0.1:5174"
    monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", origin)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        response = await client.post(
            "/api/auth/register",
            json={"email": EMAIL, "password": PASSWORD},
            headers={"Origin": origin},
        )
    assert response.status_code == 201


@pytest.mark.anyio
async def test_registration_origin_uses_dotenv_after_stale_inherited_value_is_cleared(
    auth_client, monkeypatch, tmp_path
):
    payload = {"email": "recruiter@localhost", "password": PASSWORD}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        # Uvicorn's env-file loader does not replace variables already inherited
        # by the process. A stale explicit list therefore excludes the new port.
        monkeypatch.setenv(
            "SKILLFAIR_AUTH_ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
        stale_response = await client.post(
            "/api/auth/register",
            json=payload,
            headers={"Origin": VITE_ORIGIN},
        )
        assert stale_response.status_code == 403

        # run-local.ps1 clears the inherited value before Uvicorn loads .env.
        env_file = tmp_path / "backend.env"
        env_file.write_text(
            "SKILLFAIR_AUTH_ALLOWED_ORIGINS="
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS")
        assert load_dotenv(env_file, override=False)
        accepted_response = await client.post(
            "/api/auth/register",
            json=payload,
            headers={"Origin": VITE_ORIGIN},
        )
    assert accepted_response.status_code == 422
    assert accepted_response.json() == {"detail": "Enter a valid email address."}


@pytest.mark.anyio
async def test_login_accepts_the_configured_vite_5174_origin(auth_client, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await _register(client)).status_code == 201
        monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", VITE_ORIGIN)
        response = await client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
            headers={"Origin": VITE_ORIGIN},
        )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_registration_accepts_matching_referer_when_origin_is_absent(auth_client, monkeypatch):
    monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", VITE_ORIGIN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        response = await client.post(
            "/api/auth/register",
            json={"email": EMAIL, "password": PASSWORD},
            headers={"Referer": f"{VITE_ORIGIN}/signup"},
        )
    assert response.status_code == 201


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_registration_normalizes_email_hashes_password_and_sets_secure_cookies(auth_client):
    engine = auth_client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        response = await _register(client)
        assert response.status_code == 201
        assert response.json()["email"] == "recruiter@example.test"
        assert "skillfair_session" not in response.text
        assert "password_hash" not in response.text
        session_cookie = client.cookies.get(SESSION_COOKIE_NAME)
        csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
        assert session_cookie and csrf_cookie
        session = _session_for_cookie(engine, session_cookie)
        assert session.token_hash == hashlib.sha256(session_cookie.encode()).hexdigest()
        assert session.csrf_token_hash == hashlib.sha256(csrf_cookie.encode()).hexdigest()
        assert session_cookie not in session.token_hash

    with Session(engine) as db:
        user = db.scalar(select(User).where(User.email_normalized == "recruiter@example.test"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert PasswordHasher().verify(user.password_hash, PASSWORD)
        assert PASSWORD not in user.password_hash

    set_cookie_headers = response.headers.get_list("set-cookie")
    session_header = next(value for value in set_cookie_headers if value.startswith(f"{SESSION_COOKIE_NAME}="))
    csrf_header = next(value for value in set_cookie_headers if value.startswith(f"{CSRF_COOKIE_NAME}="))
    assert "HttpOnly" in session_header
    assert "Secure" in session_header and "SameSite=lax" in session_header
    assert "HttpOnly" not in csrf_header
    assert "Secure" in csrf_header and "SameSite=lax" in csrf_header


@pytest.mark.anyio
async def test_duplicate_registration_is_controlled_and_does_not_create_another_user(auth_client):
    engine = auth_client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await _register(client)).status_code == 201
        duplicate = await _register(client, " RECRUITER@example.test ")
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": "Unable to create account. Check the details or sign in."}
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.anyio
async def test_successful_login_rotates_the_existing_session_identifier(auth_client):
    engine = auth_client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await _register(client)).status_code == 201
        old_token = client.cookies.get(SESSION_COOKIE_NAME)
        login = await client.post("/api/auth/login", json={"email": "RECRUITER@example.test", "password": PASSWORD}, headers=_origin_headers())
        assert login.status_code == 200
        new_token = client.cookies.get(SESSION_COOKIE_NAME)
        assert new_token and new_token != old_token
        assert SESSION_COOKIE_NAME not in login.text
        old_session = _session_for_cookie(engine, old_token)
        new_session = _session_for_cookie(engine, new_token)
        assert old_session.revoked_at is not None
        assert new_session.revoked_at is None


@pytest.mark.anyio
async def test_invalid_login_is_generic_for_unknown_and_incorrect_credentials(auth_client):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await _register(client)).status_code == 201
        client.cookies.clear()
        wrong_password = await client.post("/api/auth/login", json={"email": EMAIL, "password": "incorrect password"}, headers=_origin_headers())
        unknown_email = await client.post("/api/auth/login", json={"email": "missing@example.test", "password": "incorrect password"}, headers=_origin_headers())
        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json() == unknown_email.json() == {"detail": "Invalid email or password."}


@pytest.mark.anyio
async def test_current_user_requires_and_validates_a_session(auth_client):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await client.get("/api/auth/me")).status_code == 401
        registered = await _register(client)
        response = await client.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["id"] == registered.json()["id"]
        assert set(response.json()) == {"id", "email", "created_at"}


@pytest.mark.anyio
async def test_missing_session_returns_401_without_database_configuration(auth_client, monkeypatch):
    auth_test_application.dependency_overrides.pop(database.get_db_session, None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}


@pytest.mark.anyio
async def test_presented_session_with_unavailable_database_returns_safe_503(auth_client, monkeypatch):
    auth_test_application.dependency_overrides.pop(database.get_db_session, None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        client.cookies.set(SESSION_COOKIE_NAME, "opaque-invalid-locator")
        response = await client.get("/api/auth/me")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is not configured for this operation."}


@pytest.mark.anyio
async def test_expired_idle_and_revoked_sessions_are_rejected(auth_client):
    engine = auth_client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await _register(client)).status_code == 201
        token = client.cookies.get(SESSION_COOKIE_NAME)
        session = _session_for_cookie(engine, token)
        with Session(engine) as db:
            record = db.get(UserSession, session.id)
            record.idle_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        assert (await client.get("/api/auth/me")).status_code == 401
        assert _session_for_cookie(engine, token).revoked_at is not None

        client.cookies.clear()
        assert (await _register(client, "second@example.test")).status_code == 201
        second_token = client.cookies.get(SESSION_COOKIE_NAME)
        second = _session_for_cookie(engine, second_token)
        with Session(engine) as db:
            record = db.get(UserSession, second.id)
            record.revoked_at = datetime.now(timezone.utc)
            db.commit()
        assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.anyio
async def test_absolute_expiry_is_enforced_even_if_idle_expiry_is_future(auth_client):
    engine = auth_client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await _register(client)).status_code == 201
        token = client.cookies.get(SESSION_COOKIE_NAME)
        session = _session_for_cookie(engine, token)
        with Session(engine) as db:
            record = db.get(UserSession, session.id)
            record.absolute_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            record.idle_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            db.commit()
        assert (await client.get("/api/auth/me")).status_code == 401
        assert _session_for_cookie(engine, token).revoked_at is not None


@pytest.mark.anyio
async def test_logout_requires_origin_and_matching_double_submit_csrf_then_revokes_session(auth_client, monkeypatch):
    engine = auth_client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        assert (await _register(client)).status_code == 201
        token = client.cookies.get(SESSION_COOKIE_NAME)
        csrf = client.cookies.get(CSRF_COOKIE_NAME)

        missing = await client.post("/api/auth/logout", headers=_origin_headers())
        assert missing.status_code == 403
        wrong = await client.post("/api/auth/logout", headers={**_origin_headers(), "X-CSRF-Token": "wrong"})
        assert wrong.status_code == 403
        client.cookies.set(CSRF_COOKIE_NAME, "forged-but-matching", path="/")
        forged = await client.post(
            "/api/auth/logout",
            headers={**_origin_headers(), "X-CSRF-Token": "forged-but-matching"},
        )
        assert forged.status_code == 403
        client.cookies.set(CSRF_COOKIE_NAME, csrf, path="/")
        denied_origin = await client.post("/api/auth/logout", headers={"Origin": "https://attacker.test", "X-CSRF-Token": csrf})
        assert denied_origin.status_code == 403

        monkeypatch.setenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS", VITE_ORIGIN)
        logout = await client.post("/api/auth/logout", headers={"Origin": VITE_ORIGIN, "X-CSRF-Token": csrf})
        assert logout.status_code == 204
        assert _session_for_cookie(engine, token).revoked_at is not None
        assert (await client.get("/api/auth/me")).status_code == 401


@pytest.mark.anyio
async def test_registration_rejects_untrusted_or_missing_origin(auth_client):
    engine = auth_client
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_test_application), base_url=ORIGIN) as client:
        payload = {"email": EMAIL, "password": PASSWORD}
        assert (await client.post("/api/auth/register", json=payload, headers={"Origin": "https://attacker.test"})).status_code == 403
        assert (await client.post("/api/auth/login", json=payload, headers={"Origin": "https://attacker.test"})).status_code == 403
        assert (await client.post("/api/auth/register", json=payload)).status_code == 403
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
