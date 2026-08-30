"""Health endpoint: the versioned route and its deprecated pre-v1 alias."""

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app


def test_v1_health_returns_ok(client: TestClient, settings: Settings) -> None:
    response = client.get(f"{settings.api_v1_prefix}/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == settings.app_name
    assert body["environment"] == settings.app_env
    assert body["version"]


def test_legacy_health_matches_v1(client: TestClient, settings: Settings) -> None:
    """The pre-v1 alias stays behaviourally identical while it exists."""
    legacy = client.get(f"{settings.api_prefix}/health")
    versioned = client.get(f"{settings.api_v1_prefix}/health")

    assert legacy.status_code == 200
    assert legacy.json() == versioned.json()


def test_legacy_health_is_marked_deprecated(client: TestClient, settings: Settings) -> None:
    schema = client.get("/openapi.json").json()

    legacy = schema["paths"][f"{settings.api_prefix}/health"]["get"]
    versioned = schema["paths"][f"{settings.api_v1_prefix}/health"]["get"]

    assert legacy["deprecated"] is True
    assert versioned.get("deprecated", False) is False


def test_legacy_health_can_be_disabled() -> None:
    """Removing the alias is a config change, not a code change."""
    settings = get_settings().model_copy(update={"enable_legacy_health_route": False})

    with TestClient(create_app(settings)) as client:
        assert client.get(f"{settings.api_prefix}/health").status_code == 404
        assert client.get(f"{settings.api_v1_prefix}/health").status_code == 200
