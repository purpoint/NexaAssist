"""Smoke test: the application boots and the health endpoint responds."""

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_health_returns_ok() -> None:
    settings = get_settings()
    client = TestClient(create_app())

    response = client.get(f"{settings.api_prefix}/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == settings.app_name
    assert body["environment"] == settings.app_env
    assert body["version"]
