"""Lazy SQLAlchemy configuration and FastAPI session lifecycle.

Importing this module never reads credentials into logs or opens a connection.
The first database operation requires DATABASE_URL to be configured.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Generator

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

from .models import DatabaseHealthResponse


class DatabaseConfigurationError(RuntimeError):
    """Safe configuration error that never includes the supplied URL."""


_POOL_SIZE = 5
_MAX_OVERFLOW = 5
_POOL_TIMEOUT_SECONDS = 10
_POOL_RECYCLE_SECONDS = 1800
_engine_lock = threading.Lock()
_engine: Engine | None = None
_engine_url: str | None = None
_session_factory: sessionmaker[Session] | None = None


def _normalize_database_url(value: str) -> str:
    try:
        url = make_url(value.strip())
    except (ArgumentError, TypeError, ValueError) as exc:
        raise DatabaseConfigurationError("DATABASE_URL is invalid.") from exc

    if url.drivername in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        url = url.set(drivername="postgresql+psycopg")
        if not url.database:
            raise DatabaseConfigurationError("DATABASE_URL must identify a PostgreSQL database.")
    elif url.drivername.startswith("sqlite"):
        # SQLite is supported for isolated tests and local metadata checks only.
        return value.strip()
    else:
        raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL or SQLite.")
    return url.render_as_string(hide_password=False)


def get_database_url() -> str | None:
    """Return a validated URL, or None when the optional DB is not configured."""
    raw = os.getenv("DATABASE_URL")
    if raw is None or not raw.strip():
        return None
    return _normalize_database_url(raw)


def get_migration_database_url() -> str | None:
    """Return the separately configured Alembic URL; never fall back to runtime credentials."""
    raw = os.getenv("SKILLFAIR_MIGRATION_DATABASE_URL")
    if raw is None or not raw.strip():
        return None
    return _normalize_database_url(raw)


def create_database_engine(database_url: str | None = None, **options: object) -> Engine:
    """Create an engine without connecting; parameters are overridable in tests."""
    selected = database_url if database_url is not None else get_database_url()
    if selected is None:
        raise DatabaseConfigurationError("Database is not configured. Set DATABASE_URL to enable database operations.")
    safe_url = _normalize_database_url(selected)
    url = make_url(safe_url)
    engine_options: dict[str, object] = {"pool_pre_ping": True, "echo": False}
    if url.drivername.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
        if url.database in {None, "", ":memory:"}:
            engine_options["poolclass"] = StaticPool
    else:
        engine_options.update({
            "pool_size": _POOL_SIZE,
            "max_overflow": _MAX_OVERFLOW,
            "pool_timeout": _POOL_TIMEOUT_SECONDS,
            "pool_recycle": _POOL_RECYCLE_SECONDS,
        })
    engine_options.update(options)
    engine = create_engine(url, **engine_options)
    if url.drivername.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: object, _connection_record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def get_database_engine() -> Engine:
    """Return a cached engine, lazily created and without opening a connection."""
    global _engine, _engine_url, _session_factory
    database_url = get_database_url()
    if database_url is None:
        raise DatabaseConfigurationError("Database is not configured. Set DATABASE_URL to enable database operations.")
    with _engine_lock:
        if _engine is None or _engine_url != database_url:
            if _engine is not None:
                _engine.dispose()
            _engine = create_database_engine(database_url)
            _engine_url = database_url
            _session_factory = None
        return _engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency: provide one session, rollback failures, always close."""
    global _session_factory
    try:
        engine = get_database_engine()
    except DatabaseConfigurationError as exc:
        raise HTTPException(status_code=503, detail="Database is not configured for this operation.") from exc
    with _engine_lock:
        if _session_factory is None or _session_factory.kw.get("bind") is not engine:
            _session_factory = create_session_factory(engine)
        factory = _session_factory
    session = factory()
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_health() -> DatabaseHealthResponse:
    """Check reachability on demand; an unconfigured database is not app-fatal."""
    try:
        if get_database_url() is None:
            return DatabaseHealthResponse(status="unconfigured", configured=False, reachable=False)
        engine = get_database_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return DatabaseHealthResponse(status="reachable", configured=True, reachable=True)
    except (DatabaseConfigurationError, SQLAlchemyError):
        return DatabaseHealthResponse(status="unavailable", configured=True, reachable=False)


def dispose_database_engine() -> None:
    """Dispose the process pool at application shutdown; safe if never created."""
    global _engine, _engine_url, _session_factory
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_url = None
        _session_factory = None
