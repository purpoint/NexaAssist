"""Health endpoint, and the absence of the pre-v1 alias it used to have.

The alias was removed once a real consumer existed and did not use it. These
guards are the removal's premise tests, updated rather than deleted: they now
pin that it is gone and that the setting which gated it cannot come back by
accident.
"""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_v1_health_returns_ok(client: TestClient, settings: Settings) -> None:
    response = client.get(f"{settings.api_v1_prefix}/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == settings.app_name
    assert body["environment"] == settings.app_env
    assert body["version"]


def test_the_pre_v1_alias_is_gone(client: TestClient, settings: Settings) -> None:
    """It answered from M0 to M21; the versioned route is the only one now."""
    assert client.get(f"{settings.api_prefix}/health").status_code == 404
    assert client.get(f"{settings.api_v1_prefix}/health").status_code == 200


def test_the_alias_is_absent_from_the_document(
    client: TestClient, settings: Settings
) -> None:
    """Removed, not merely hidden -- a deprecated entry would still be a promise."""
    paths = client.get("/openapi.json").json()["paths"]

    assert f"{settings.api_prefix}/health" not in paths
    assert paths[f"{settings.api_v1_prefix}/health"]["get"].get("deprecated", False) is False


def test_the_gate_that_served_it_is_gone() -> None:
    """Leaving the setting behind would invite the route back with it."""
    assert "enable_legacy_health_route" not in Settings.model_fields

    # Still accepted as input and ignored, because `extra="ignore"` is the
    # configured behaviour: an operator with the old variable still in their
    # environment gets a service that starts, not one that refuses to.
    with TestClient(create_app(Settings())) as client:
        assert client.get("/api/health").status_code == 404
