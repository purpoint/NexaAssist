"""Fixtures for the tests that talk to a real Redis server.

Three jobs, all scoped to this directory only:

* Opt out of the suite-wide autouse ``no_network`` guard, the same way the
  database package does -- by overriding the fixture by name here rather than
  editing the shared conftest.
* Skip everything when no server is reachable, so the suite still passes on a
  machine without one.
* Confine every key to one namespace on one database index.

The confinement is not decoration. A Redis server is far more likely to be
shared between projects than a PostgreSQL database is, and ``FLUSHDB`` is one
keystroke from destroying somebody else's data. Nothing here ever flushes:
cleanup scans for the test prefix and deletes only what matches.
"""

import os
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import pytest
from redis.asyncio import Redis

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
TEST_NAMESPACE = "nexaassist:test:jobs"
EXPECTED_DB_INDEX = 15


def redis_reachable(url: str = TEST_REDIS_URL) -> bool:
    """Can we open a TCP connection to the configured host?"""
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Narrow the suite-wide guard rather than removing it.

    These tests need real sockets -- PostgreSQL is a real server -- so the
    inherited ``no_network`` fixture cannot stand. Replacing it with "anything
    goes" is what let a test reach api.groq.com with the developer's real key,
    billed and unnoticed, because the one guard that would have caught it was
    the one this package switched off.

    So: localhost only. A database and a Redis on this machine are reachable;
    anything off it is not.
    """

    def blocked(host: object) -> None:
        raise RuntimeError(
            f"Only local connections are allowed in the test suite; got {host!r}."
        )

    def check(address: object) -> None:
        if isinstance(address, tuple) and address:
            host = address[0]
            if host not in {"localhost", "127.0.0.1", "::1", ""}:
                blocked(host)

    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def guarded_connect(self: socket.socket, address: object) -> object:
        check(address)
        return real_connect(self, address)

    def guarded_create(address: object, *args: object, **kwargs: object) -> object:
        check(address)
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create)


@pytest.fixture(autouse=True)
def require_redis() -> None:
    if not redis_reachable():
        pytest.skip(f"Redis not reachable at {TEST_REDIS_URL}")


@pytest.fixture(autouse=True)
def _guard_target() -> None:
    """Refuse to run anywhere but the dedicated test database index."""
    assert urlparse(TEST_REDIS_URL).path == f"/{EXPECTED_DB_INDEX}"
    assert TEST_NAMESPACE.startswith("nexaassist:test:")


@pytest.fixture
async def client() -> AsyncIterator[Redis]:
    """A client on the test index, with the test namespace emptied around it."""
    connection = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await _delete_namespace(connection)
    try:
        yield connection
    finally:
        await _delete_namespace(connection)
        await connection.aclose()


async def _delete_namespace(connection: Redis) -> None:
    """Delete only keys under the test prefix.

    ``scan_iter`` with an explicit match, never ``FLUSHDB``: this server may
    belong to something else entirely.
    """
    keys = [key async for key in connection.scan_iter(match=f"{TEST_NAMESPACE}:*")]
    if keys:
        await connection.delete(*keys)
