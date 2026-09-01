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

import os
import socket
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlparse

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://localhost:5432/nexaassist_test"
)
EXPECTED_DATABASE = "nexaassist_test"


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
