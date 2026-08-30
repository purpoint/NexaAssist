"""Application creation, schema exposure, and error rendering."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import __version__
from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.main import create_app


def test_create_app_returns_configured_application(settings: Settings) -> None:
    app = create_app(settings)

    assert isinstance(app, FastAPI)
    assert app.title == settings.app_name
    assert app.version == __version__


def test_create_app_uses_default_settings_when_none_given() -> None:
    assert isinstance(create_app(), FastAPI)


def test_openapi_exposes_versioned_health(client: TestClient, settings: Settings) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == settings.app_name
    assert f"{settings.api_v1_prefix}/health" in schema["paths"]


def test_docs_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_unknown_route_returns_404_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "Not Found"}


def test_app_error_is_rendered_as_error_response(settings: Settings) -> None:
    """AppError subclasses map to their status code and the shared error body."""
    app = create_app(settings)

    @app.get("/raises")
    def raises() -> None:
        raise NotFoundError("Ticket 42 does not exist.", details={"ticket_id": 42})

    with TestClient(app) as client:
        response = client.get("/raises")

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Ticket 42 does not exist.",
        "details": {"ticket_id": 42},
    }
