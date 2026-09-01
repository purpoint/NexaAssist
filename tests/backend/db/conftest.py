"""Fixtures for the tests that talk to a real database.

Two jobs, both scoped to this directory only:

* Opt out of the suite-wide autouse ``no_network`` guard. Overriding the
  fixture by name here is what lets these tests open a socket without editing
  the shared conftest, which stays exactly as it was.
* Skip everything when PostgreSQL is not reachable, so the suite still passes
  on a machine or CI runner without a database.

Every statement issued from this package targets ``nexaassist_test`` and
nothing else. The URL is a fixed constant, never discovered by enumerating
databases on the server.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://localhost:5432/nexaassist_test"
)
EXPECTED_DATABASE = "nexaassist_test"

BACKEND = Path(__file__).resolve().parents[3] / "backend"

DOMAIN_TABLES = ("review_items", "document_chunks", "documents", "tickets", "customers")
"""Written out literally, children first.

Never discovered by querying ``information_schema``: a truncate driven by a
lookup is one bad WHERE clause away from emptying something it was never meant
to touch.
"""


def postgres_reachable(url: str = TEST_DATABASE_URL) -> bool:
    """Can we open a TCP connection to the configured host?"""
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def no_network() -> None:
    """Override the inherited guard for this package only.

    Resolution picks the definition closest to the test, so the guard stays
    fully active for every other directory.
    """
    return None


@pytest.fixture(autouse=True)
def require_postgres() -> None:
    if not postgres_reachable():
        pytest.skip(f"PostgreSQL not reachable for {EXPECTED_DATABASE}")


@pytest.fixture
def test_database_url() -> str:
    return TEST_DATABASE_URL


@pytest.fixture
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    """An engine bound to the test database, disposed afterwards."""
    created = create_async_engine(test_database_url, pool_pre_ping=True)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _guard_module_import() -> Iterator[None]:
    """Fail loudly if a test ever points at a database other than the test one."""
    assert EXPECTED_DATABASE in TEST_DATABASE_URL
    yield


def alembic_config(url: str = TEST_DATABASE_URL) -> Config:
    """Alembic config pointed explicitly at the test database."""
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


async def _confirm_target_database() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            current = await connection.scalar(text("SELECT current_database()"))
    finally:
        await engine.dispose()
    if current != EXPECTED_DATABASE:
        raise RuntimeError(
            f"refusing to run schema operations against {current!r}; "
            f"expected {EXPECTED_DATABASE!r}"
        )


async def _truncate_domain_tables() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(f"TRUNCATE {', '.join(DOMAIN_TABLES)} RESTART IDENTITY")
            )
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_schema() -> None:
    """Bring the test database to head, once, after confirming the target.

    Session-scoped fixtures are set up before function-scoped autouse ones, so
    the reachability check has to be repeated here: without it an unreachable
    database turns every test in this package into an error instead of a skip.
    """
    if not postgres_reachable():
        pytest.skip(f"PostgreSQL not reachable for {EXPECTED_DATABASE}")
    asyncio.run(_confirm_target_database())
    command.upgrade(alembic_config(), "head")


@pytest.fixture
def clean_tables(migrated_schema: None) -> None:
    """Start each test from empty domain tables.

    Re-asserts head first so this holds regardless of test ordering -- the
    migration tests downgrade and restore around themselves.
    """
    command.upgrade(alembic_config(), "head")
    asyncio.run(_truncate_domain_tables())
