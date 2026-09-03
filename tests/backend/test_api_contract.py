"""The published HTTP contract.

Asserts what clients are entitled to rely on, so a refactor that quietly
renames a field or drops an endpoint fails here rather than in somebody's
integration.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.assistant import AssistantMessageResponse
from app.schemas.common import ErrorResponse

EXPECTED_PATHS = {
    "/api/health",
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/intent/analyze",
    "/api/v1/tickets",
    "/api/v1/tickets/{ticket_id}",
    "/api/v1/documents",
    "/api/v1/documents/answer",
    "/api/v1/documents/{document_id}",
    "/api/v1/assistant/messages",
    "/api/v1/conversations",
    "/api/v1/conversations/{conversation_id}/messages",
}


@pytest.fixture
def spec(client: TestClient) -> dict:
    return client.get("/openapi.json").json()


def test_the_published_paths_are_exactly_these(spec: dict) -> None:
    """Pinned. Adding a path is a decision; noticing one appeared is not."""
    assert set(spec["paths"]) == EXPECTED_PATHS


def test_the_pre_v1_health_alias_is_still_served_and_deprecated(spec: dict) -> None:
    """Scheduled for removal, but not yet -- M18 is contract work only."""
    assert spec["paths"]["/api/health"]["get"]["deprecated"] is True


def test_every_operation_has_a_summary(spec: dict) -> None:
    missing = [
        f"{method} {path}"
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
        if not operation.get("summary")
    ]
    assert missing == []


def test_the_websocket_route_is_still_absent(spec: dict) -> None:
    """FastAPI documents HTTP operations; M14's contract lives in its tests."""
    assert "/api/v1/ws" not in spec["paths"]


def test_no_internal_type_is_published(spec: dict) -> None:
    """The service's own value types are not part of the contract."""
    published = set(spec["components"]["schemas"])
    assert "AssistantReply" not in published
    assert "RoutedReply" not in published
    assert "HandoffResult" not in published
    assert "AgentDecision" not in published


def test_the_assistant_response_is_the_published_shape(spec: dict) -> None:
    schema = spec["components"]["schemas"]["AssistantMessageResponse"]
    assert set(schema["properties"]) == set(AssistantMessageResponse.model_fields)


def test_the_assistant_message_is_required_and_bounded(spec: dict) -> None:
    schema = spec["components"]["schemas"]["AssistantMessageRequest"]
    assert schema["required"] == ["message"]
    assert schema["properties"]["message"]["maxLength"] == 8000
    assert schema.get("additionalProperties") is False


def test_the_conversation_id_is_optional(spec: dict) -> None:
    """An existing client that never sends one keeps working."""
    schema = spec["components"]["schemas"]["AssistantMessageRequest"]
    assert "conversation_id" not in schema["required"]


def test_error_responses_are_declared_where_they_can_happen(spec: dict) -> None:
    ready = spec["paths"]["/api/v1/ready"]["get"]["responses"]
    assistant = spec["paths"]["/api/v1/assistant/messages"]["post"]["responses"]
    history = spec["paths"]["/api/v1/conversations/{conversation_id}/messages"]["get"][
        "responses"
    ]
    assert "503" in ready
    assert "503" in assistant
    assert "404" in history


def test_one_error_shape_is_used_throughout(spec: dict) -> None:
    """A client parses one error format, not one per endpoint."""
    assert set(spec["components"]["schemas"]["ErrorResponse"]["properties"]) == set(
        ErrorResponse.model_fields
    )


def test_the_spec_is_deterministic(client: TestClient) -> None:
    assert client.get("/openapi.json").json() == client.get("/openapi.json").json()


def test_two_apps_produce_the_same_spec() -> None:
    """No ordering that depends on import or construction time."""
    with TestClient(create_app()) as first, TestClient(create_app()) as second:
        assert first.get("/openapi.json").json() == second.get("/openapi.json").json()


def test_health_is_unauthenticated_and_cheap(client: TestClient) -> None:
    """M1's contract: liveness must answer even when dependencies are down."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_an_unknown_path_uses_the_shared_error_shape(client: TestClient) -> None:
    body = client.get("/api/v1/nope").json()
    assert set(body) <= set(ErrorResponse.model_fields)
    assert body["code"] == "not_found"
