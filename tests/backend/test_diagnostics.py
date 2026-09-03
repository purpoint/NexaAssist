"""Component diagnostics: reported honestly, and revealing nothing."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app
from app.observability.diagnostics import (
    authentication_status,
    job_queue_status,
    model_provider_status,
    rate_limiter_status,
)
from app.schemas.readiness import ComponentStatus, ReadinessResponse

READY = "/api/v1/ready"
KEYS = "web:0123456789abcdef0"


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """The probes read cached settings; each test configures its own."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def configured(**values: object) -> None:
    """Install a settings object the probes will see."""
    settings = Settings(**values)
    get_settings.cache_clear()
    get_settings.__wrapped__ = lambda: settings  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# The vocabulary


def test_degraded_is_distinct_from_unavailable() -> None:
    """Only 'unavailable' takes the service out of rotation."""
    assert ComponentStatus.DEGRADED != ComponentStatus.UNAVAILABLE
    assert ComponentStatus.DEGRADED.value == "degraded"


def test_the_response_defaults_to_no_components() -> None:
    """So an existing client that never reads them is unaffected."""
    assert ReadinessResponse(database=ComponentStatus.OK).components == {}


# --------------------------------------------------------------------------
# The probes


@pytest.mark.anyio
async def test_an_in_memory_queue_is_reported_as_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It works, but it is not a durable shared queue, and that matters."""
    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings())
    import app.observability.diagnostics as module

    monkeypatch.setattr(module, "__name__", module.__name__)
    assert await job_queue_status() is ComponentStatus.NOT_CONFIGURED


def test_the_static_provider_is_reported_as_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: Settings(llm_provider="static")
    )
    assert model_provider_status() is ComponentStatus.NOT_CONFIGURED


def test_a_provider_without_a_key_is_degraded_not_broken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK may still resolve one from the environment."""
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(llm_provider="groq", llm_api_key=None),
    )
    assert model_provider_status() is ComponentStatus.DEGRADED


def test_a_configured_provider_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(llm_provider="groq", llm_api_key="test-key-not-real"),
    )
    assert model_provider_status() is ComponentStatus.OK


def test_limiting_and_authentication_report_whether_they_are_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings())
    assert rate_limiter_status() is ComponentStatus.NOT_CONFIGURED
    assert authentication_status() is ComponentStatus.NOT_CONFIGURED

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(
            rate_limit_provider="memory", auth_provider="api_key", auth_api_keys=KEYS
        ),
    )
    assert rate_limiter_status() is ComponentStatus.OK
    assert authentication_status() is ComponentStatus.OK


@pytest.mark.anyio
async def test_a_failing_queue_probe_is_degraded_not_an_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A probe that raises is a probe that took the service down with it."""
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(job_queue="redis", redis_url="redis://localhost:6379/0"),
    )

    class Exploding:
        async def ping(self) -> bool:
            raise RuntimeError("redis://user:pw@cache.internal:6379 refused")

    monkeypatch.setattr("app.jobs.factory.get_job_queue", lambda: Exploding())

    with caplog.at_level(logging.WARNING, logger="app.observability.diagnostics"):
        assert await job_queue_status() is ComponentStatus.DEGRADED
    assert "user:pw" not in caplog.text
    assert "cache.internal" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.anyio
async def test_an_unreachable_queue_is_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: Settings(job_queue="redis", redis_url="redis://localhost:6379/0"),
    )

    class Down:
        async def ping(self) -> bool:
            return False

    monkeypatch.setattr("app.jobs.factory.get_job_queue", lambda: Down())
    assert await job_queue_status() is ComponentStatus.DEGRADED


# --------------------------------------------------------------------------
# Over HTTP


def test_readiness_reports_every_component(client: TestClient) -> None:
    body = client.get(READY).json()
    assert set(body["components"]) == {
        "database",
        "job_queue",
        "model_provider",
        "rate_limiter",
        "authentication",
    }


def test_the_existing_readiness_contract_is_unchanged(client: TestClient) -> None:
    """M3's fields are still there and still mean the same thing."""
    body = client.get(READY).json()
    assert body["status"] == "ready"
    assert body["database"] in {s.value for s in ComponentStatus}
    assert body["database"] == body["components"]["database"]


def test_a_degraded_component_does_not_make_the_service_unready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a working process over a degraded feature is the worse outage."""
    monkeypatch.setattr(
        "app.observability.diagnostics.job_queue_status",
        _degraded,
    )
    import app.api.v1.readiness as readiness_module

    monkeypatch.setattr(readiness_module, "job_queue_status", _degraded)

    with TestClient(create_app()) as client:
        response = client.get(READY)
    assert response.status_code == 200
    assert response.json()["components"]["job_queue"] == "degraded"


async def _degraded() -> ComponentStatus:
    return ComponentStatus.DEGRADED


def test_no_diagnostic_reveals_a_host_or_a_credential(client: TestClient) -> None:
    body = client.get(READY).text
    for leak in ("postgresql", "redis://", "localhost", "5432", "6379", "gsk_", "@"):
        assert leak not in body


def test_liveness_is_unaffected_by_diagnostics(client: TestClient) -> None:
    """M1's liveness body is untouched -- it reports no component at all."""
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert "components" not in body
