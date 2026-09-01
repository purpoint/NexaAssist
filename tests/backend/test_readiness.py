"""The readiness endpoint, driven offline.

The probe is stubbed here so every branch is exercised without a database. The
real round trip lives in ``tests/backend/db/test_integration.py``.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db.errors import DatabaseNotConfiguredError
from app.main import create_app
from app.schemas.readiness import ComponentStatus, ReadinessResponse

READY_URL = "/api/v1/ready"


def client_with_status(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, status: ComponentStatus
) -> Iterator[TestClient]:
    import app.api.v1.readiness as readiness_module

    async def probe() -> ComponentStatus:
        return status

    # Patch the name bound in the route module: `from ... import` binds at
    # import time, so patching app.db.health would have no effect here.
    monkeypatch.setattr(readiness_module, "database_status", probe)
    with TestClient(create_app(settings)) as client:
        yield client


# --------------------------------------------------------------------------
# Ready
# --------------------------------------------------------------------------


def test_ready_when_the_database_is_reachable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    for client in client_with_status(monkeypatch, settings, ComponentStatus.OK):
        response = client.get(READY_URL)

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_ready_when_no_database_is_configured(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Running without a database is a supported mode, not a fault."""
    for client in client_with_status(
        monkeypatch, settings, ComponentStatus.NOT_CONFIGURED
    ):
        response = client.get(READY_URL)

    assert response.status_code == 200
    assert response.json()["database"] == "not_configured"


# --------------------------------------------------------------------------
# Not ready
# --------------------------------------------------------------------------


def test_unavailable_database_makes_the_service_unready(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    for client in client_with_status(
        monkeypatch, settings, ComponentStatus.UNAVAILABLE
    ):
        response = client.get(READY_URL)

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "database_unavailable"
    assert set(body) <= {"code", "message", "details"}


def test_unready_response_leaks_no_connection_details(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """A database error body must never carry a host, user, or URL."""
    for client in client_with_status(
        monkeypatch, settings, ComponentStatus.UNAVAILABLE
    ):
        body = client.get(READY_URL).json()

    rendered = str(body)
    for leak in ("postgresql", "asyncpg", "@", "5432", "password"):
        assert leak not in rendered, leak
    assert body["details"] == {"component": "database"}


# --------------------------------------------------------------------------
# Liveness is untouched
# --------------------------------------------------------------------------


def test_health_still_reports_liveness_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """Liveness must not follow readiness, or an orchestrator restarts a
    healthy process because a dependency blipped."""
    for client in client_with_status(
        monkeypatch, settings, ComponentStatus.UNAVAILABLE
    ):
        health = client.get("/api/v1/health")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"


def test_health_contract_is_unchanged(client: TestClient, settings: Settings) -> None:
    body = client.get(f"{settings.api_v1_prefix}/health").json()

    assert set(body) == {"status", "service", "environment", "version"}


# --------------------------------------------------------------------------
# Probe and schema
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_probe_reports_not_configured_without_a_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db.health as health_module

    def unconfigured() -> None:
        raise DatabaseNotConfiguredError()

    monkeypatch.setattr(health_module, "get_engine", unconfigured)

    assert await health_module.database_status() == ComponentStatus.NOT_CONFIGURED


@pytest.mark.anyio
async def test_probe_reports_unavailable_when_connecting_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.db.health as health_module
    from sqlalchemy.exc import OperationalError

    class Failing:
        def connect(self) -> None:
            raise OperationalError("SELECT 1", {}, Exception("boom"))

    monkeypatch.setattr(health_module, "get_engine", lambda: Failing())

    assert await health_module.database_status() == ComponentStatus.UNAVAILABLE


def test_component_status_values() -> None:
    assert [s.value for s in ComponentStatus] == ["ok", "not_configured", "unavailable"]


def test_readiness_response_defaults_to_ready() -> None:
    assert ReadinessResponse(database=ComponentStatus.OK).status == "ready"
