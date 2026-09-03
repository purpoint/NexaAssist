"""End-to-end checks against a real PostgreSQL instance.

Everything here targets ``nexaassist_test``. Any DDL is created and dropped
inside the test that needs it, or rolled back with its transaction.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.engine import build_engine
from app.main import create_app
from app.schemas.readiness import ComponentStatus

from .conftest import EXPECTED_DATABASE

READY_URL = "/api/v1/ready"


# --------------------------------------------------------------------------
# Readiness against a live database
# --------------------------------------------------------------------------


def test_ready_reports_ok_against_a_live_database(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path: HTTP request -> probe -> real SELECT 1 -> 200."""
    import app.db.health as health_module

    engine = build_engine(Settings(database_url=test_database_url))
    monkeypatch.setattr(health_module, "get_engine", lambda: engine)

    with TestClient(create_app(Settings(database_url=test_database_url))) as client:
        response = client.get(READY_URL)

    assert response.status_code == 200
    body = response.json()
    # The M3 contract is unchanged; M20 added the components report beside it.
    assert body["status"] == "ready"
    assert body["database"] == "ok"
    assert body["components"]["database"] == "ok"


def test_ready_reports_unavailable_against_a_dead_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real connection failure, not a stubbed one."""
    import app.db.health as health_module

    dead = build_engine(
        Settings(database_url="postgresql+asyncpg://localhost:59999/nexaassist_test")
    )
    monkeypatch.setattr(health_module, "get_engine", lambda: dead)

    with TestClient(create_app(Settings())) as client:
        response = client.get(READY_URL)

    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"


@pytest.mark.anyio
async def test_probe_returns_ok_for_a_live_engine(engine: AsyncEngine) -> None:
    import app.db.health as health_module

    original = health_module.get_engine
    health_module.get_engine = lambda: engine  # type: ignore[assignment]
    try:
        assert await health_module.database_status() == ComponentStatus.OK
    finally:
        health_module.get_engine = original  # type: ignore[assignment]


# --------------------------------------------------------------------------
# Session semantics against a live database
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_does_not_commit_implicitly(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dependency must leave uncommitted work uncommitted.

    PostgreSQL has transactional DDL, so the probe table vanishes with the
    rollback and nothing is left behind.
    """
    from app.db import session as session_module

    built = build_engine(Settings(database_url=test_database_url))
    monkeypatch.setattr(session_module, "get_engine", lambda: built)
    session_module.get_sessionmaker.cache_clear()

    try:
        assert await _current_database(built) == EXPECTED_DATABASE

        agen = session_module.get_db_session()
        session = await agen.__anext__()
        await session.execute(text("CREATE TABLE commit_probe (id integer)"))
        await session.execute(text("INSERT INTO commit_probe VALUES (1)"))
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()  # dependency teardown: close, no commit

        assert await _table_exists(built, "commit_probe") is False
    finally:
        session_module.get_sessionmaker.cache_clear()
        await built.dispose()


@pytest.mark.anyio
async def test_session_rolls_back_when_the_caller_raises(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db import session as session_module

    built = build_engine(Settings(database_url=test_database_url))
    monkeypatch.setattr(session_module, "get_engine", lambda: built)
    session_module.get_sessionmaker.cache_clear()

    try:
        agen = session_module.get_db_session()
        session = await agen.__anext__()
        await session.execute(text("CREATE TABLE rollback_probe (id integer)"))
        with pytest.raises(RuntimeError):
            await agen.athrow(RuntimeError("handler blew up"))

        assert await _table_exists(built, "rollback_probe") is False
    finally:
        session_module.get_sessionmaker.cache_clear()
        await built.dispose()


@pytest.mark.anyio
async def test_explicit_commit_persists_and_can_be_cleaned_up(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the contract: an explicit commit does persist."""
    from app.db import session as session_module

    built = build_engine(Settings(database_url=test_database_url))
    monkeypatch.setattr(session_module, "get_engine", lambda: built)
    session_module.get_sessionmaker.cache_clear()

    try:
        agen = session_module.get_db_session()
        session = await agen.__anext__()
        await session.execute(text("CREATE TABLE explicit_probe (id integer)"))
        await session.commit()
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

        assert await _table_exists(built, "explicit_probe") is True
    finally:
        async with built.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS explicit_probe"))
        session_module.get_sessionmaker.cache_clear()
        await built.dispose()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def _current_database(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        return await connection.scalar(text("SELECT current_database()"))


async def _table_exists(engine: AsyncEngine, name: str) -> bool:
    async with engine.connect() as connection:
        found = await connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:name"
            ),
            {"name": name},
        )
    return bool(found)
