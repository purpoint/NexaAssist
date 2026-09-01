"""The connection layer against a real PostgreSQL instance."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.db.engine import build_engine
from app.db.errors import DatabaseNotConfiguredError

from .conftest import EXPECTED_DATABASE


@pytest.mark.anyio
async def test_engine_connects(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT 1")) == 1


@pytest.mark.anyio
async def test_connection_targets_only_the_test_database(engine: AsyncEngine) -> None:
    """Guards against a fixture ever pointing at another project's database."""
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT current_database()")) == (
            EXPECTED_DATABASE
        )


@pytest.mark.anyio
async def test_engine_built_from_settings_connects(test_database_url: str) -> None:
    settings = Settings(database_url=test_database_url)
    built = build_engine(settings)
    try:
        async with built.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await built.dispose()


@pytest.mark.anyio
async def test_session_dependency_yields_a_working_session(
    test_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.db import session as session_module

    built = build_engine(Settings(database_url=test_database_url))
    # Patch the name bound inside session.py, not the one in engine.py:
    # ``from ... import get_engine`` binds at import time.
    monkeypatch.setattr(session_module, "get_engine", lambda: built)
    session_module.get_sessionmaker.cache_clear()
    try:
        agen = session_module.get_db_session()
        session = await agen.__anext__()
        assert await session.scalar(text("SELECT 1")) == 1
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()
    finally:
        session_module.get_sessionmaker.cache_clear()
        await built.dispose()


def test_engine_requires_a_configured_url() -> None:
    with pytest.raises(DatabaseNotConfiguredError):
        build_engine(Settings(database_url=None))
