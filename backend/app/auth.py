"""Database-backed password authentication and opaque cookie sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_db_session
from .db_models import User, UserSession
from .models import AuthLoginRequest, AuthRegisterRequest, AuthRegistrationResponse, AuthenticatedUserResponse


SESSION_COOKIE_NAME = "skillfair_session"
CSRF_COOKIE_NAME = "skillfair_csrf"
CSRF_HEADER_NAME = "x-csrf-token"
IDLE_SESSION_SECONDS = 30 * 60
ABSOLUTE_SESSION_SECONDS = 12 * 60 * 60
_DEFAULT_ALLOWED_ORIGINS = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:5174",
    "http://127.0.0.1:5174",
}
_EMAIL_LOCAL_PART = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
_DOMAIN_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

_password_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
# Keep login timing similar for unknown email addresses without storing any user data.
_DUMMY_PASSWORD_HASH = _password_hasher.hash("skillfair-invalid-login-placeholder")

router = APIRouter(prefix="/api/auth", tags=["authentication"])


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: UserSession


def normalize_email(value: str) -> str:
    """Validate the supported email subset and produce one case-insensitive key."""
    email = value.strip()
    if len(email) > 320 or email.count("@") != 1:
        raise ValueError("invalid email")
    local, domain = email.rsplit("@", 1)
    if (
        not local
        or len(local) > 64
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not _EMAIL_LOCAL_PART.fullmatch(local)
        or not domain
    ):
        raise ValueError("invalid email")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("invalid email") from exc
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(not _DOMAIN_LABEL.fullmatch(label) for label in labels):
        raise ValueError("invalid email")
    return f"{local.casefold()}@{ascii_domain.casefold()}"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _allowed_origins() -> set[str]:
    configured = os.getenv("SKILLFAIR_AUTH_ALLOWED_ORIGINS")
    if configured is None:
        return set(_DEFAULT_ALLOWED_ORIGINS)
    origins: set[str] = set()
    for value in configured.split(","):
        candidate = value.strip().rstrip("/")
        parsed = urlsplit(candidate)
        if parsed.scheme in {"http", "https"} and parsed.netloc and not parsed.path and not parsed.query and not parsed.fragment:
            origins.add(f"{parsed.scheme.lower()}://{parsed.netloc.lower()}")
    return origins


def _require_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        referer = request.headers.get("referer")
        if referer:
            parsed_referer = urlsplit(referer)
            if parsed_referer.scheme and parsed_referer.netloc:
                origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
    normalized = origin.rstrip("/").lower() if origin else ""
    if not normalized or normalized not in _allowed_origins():
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def _cookie_secure() -> bool:
    raw = os.getenv("SKILLFAIR_AUTH_COOKIE_SECURE", "true").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=500, detail="Authentication cookie security is misconfigured.")


def _set_session_cookies(response: Response, raw_session: str, raw_csrf: str) -> None:
    secure = _cookie_secure()
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_session,
        max_age=ABSOLUTE_SESSION_SECONDS,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # A readable CSRF cookie is used only as a double-submit value; the server
    # also checks its digest against the authenticated session row.
    response.set_cookie(
        CSRF_COOKIE_NAME,
        raw_csrf,
        max_age=ABSOLUTE_SESSION_SECONDS,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _delete_session_cookies(response: Response) -> None:
    secure = _cookie_secure()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=secure, httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", secure=secure, httponly=False, samesite="lax")


def _revoke_present_session(request: Request, db: Session, now: datetime) -> None:
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw:
        return
    existing = db.scalar(select(UserSession).where(UserSession.token_hash == _digest(raw)))
    if existing is not None and existing.revoked_at is None:
        existing.revoked_at = now


def _new_session(db: Session, user: User, now: datetime) -> tuple[str, str]:
    raw_session = secrets.token_urlsafe(32)
    raw_csrf = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=_digest(raw_session),
            csrf_token_hash=_digest(raw_csrf),
            created_at=now,
            last_seen_at=now,
            idle_expires_at=now + timedelta(seconds=IDLE_SESSION_SECONDS),
            absolute_expires_at=now + timedelta(seconds=ABSOLUTE_SESSION_SECONDS),
        )
    )
    return raw_session, raw_csrf


def _user_response(user: User) -> AuthenticatedUserResponse:
    return AuthenticatedUserResponse(id=user.id, email=user.email_normalized, created_at=user.created_at)


@router.post("/register", response_model=AuthRegistrationResponse, status_code=status.HTTP_202_ACCEPTED)
def register(
    payload: AuthRegisterRequest,
    request: Request,
    db: Session = Depends(get_db_session),
) -> AuthRegistrationResponse:
    _require_allowed_origin(request)
    try:
        email = normalize_email(payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Enter a valid email address.") from exc

    user = User(email_normalized=email, password_hash=_password_hasher.hash(payload.password), is_active=True)
    try:
        db.add(user)
        db.commit()
    except IntegrityError:
        db.rollback()
    except BaseException:
        db.rollback()
        raise
    # New and existing addresses receive the same status, body, and cookie
    # behavior. New users sign in explicitly after registration.
    return AuthRegistrationResponse()


def _verify_password(user: User | None, password: str) -> bool:
    encoded_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    try:
        verified = _password_hasher.verify(encoded_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return bool(verified and user is not None and user.is_active)


@router.post("/login", response_model=AuthenticatedUserResponse)
def login(
    payload: AuthLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db_session),
) -> AuthenticatedUserResponse:
    _require_allowed_origin(request)
    try:
        email = normalize_email(payload.email)
    except ValueError:
        email = ""
    user = db.scalar(select(User).where(User.email_normalized == email)) if email else None
    if not _verify_password(user, payload.password):
        raise HTTPException(status_code=401, detail="Invalid email or password.", headers={"WWW-Authenticate": "Session"})

    now = datetime.now(timezone.utc)
    _revoke_present_session(request, db, now)
    raw_session, raw_csrf = _new_session(db, user, now)
    db.commit()
    _set_session_cookies(response, raw_session, raw_csrf)
    return _user_response(user)


def _require_session_cookie(request: Request) -> str:
    raw_session = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_session:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return raw_session


def get_auth_context(
    raw_session: str = Depends(_require_session_cookie),
    db: Session = Depends(get_db_session),
) -> AuthContext:
    session_record = db.scalar(select(UserSession).where(UserSession.token_hash == _digest(raw_session)))
    now = datetime.now(timezone.utc)
    if (
        session_record is None
        or session_record.revoked_at is not None
        or _utc(session_record.idle_expires_at) <= now
        or _utc(session_record.absolute_expires_at) <= now
    ):
        if session_record is not None and session_record.revoked_at is None:
            session_record.revoked_at = now
            db.commit()
        raise HTTPException(status_code=401, detail="Authentication required.")
    user = db.get(User, session_record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Authentication required.")

    absolute_expiry = _utc(session_record.absolute_expires_at)
    session_record.last_seen_at = now
    session_record.idle_expires_at = min(now + timedelta(seconds=IDLE_SESSION_SECONDS), absolute_expiry)
    db.commit()
    return AuthContext(user=user, session=session_record)


def get_current_user(context: AuthContext = Depends(get_auth_context)) -> User:
    return context.user


def _require_csrf(request: Request, context: AuthContext) -> None:
    _require_allowed_origin(request)
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    header_token = request.headers.get(CSRF_HEADER_NAME, "")
    if (
        not cookie_token
        or not header_token
        or not hmac.compare_digest(cookie_token, header_token)
        or not hmac.compare_digest(_digest(header_token), context.session.csrf_token_hash)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


@router.get("/me", response_model=AuthenticatedUserResponse)
def current_user(user: User = Depends(get_current_user)) -> AuthenticatedUserResponse:
    return _user_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    context: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db_session),
) -> Response:
    _require_csrf(request, context)
    context.session.revoked_at = datetime.now(timezone.utc)
    db.commit()
    _delete_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
