"""Shared fixtures for the backend test suite."""

import socket
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """The application settings the app under test is built from."""
    return get_settings()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """A client bound to a freshly built app, with lifespan events run."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` tests on asyncio.

    anyio ships its own pytest plugin and arrives transitively via Starlette,
    so async tests need no extra dependency.
    """
    return "asyncio"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open an outbound connection.

    The suite must never reach a real provider: that would be slow, flaky, and
    billable. ``TestClient`` drives the ASGI app in-process and opens no
    sockets, so nothing legitimate trips this.
    """

    def blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Network access is not allowed in the test suite.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
