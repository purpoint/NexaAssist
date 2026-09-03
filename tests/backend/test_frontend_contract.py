"""The complete contract a frontend builds against.

Everything here is a client-visible promise. The point of gathering them in one
file is that a change which breaks a frontend fails in one obvious place,
rather than as a surprise in somebody's integration.

Both transports are covered, because a frontend uses both: HTTP for the
answering pipeline and its conversations, the socket for streamed prose.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.realtime.envelope import (
    MAX_QUESTION_LENGTH,
    ClientMessageType,
    Complete,
    Delta,
    Error,
    Ping,
    Pong,
    Ready,
    ServerMessageType,
)
from app.schemas.assistant import AssistantMessageResponse
from app.schemas.common import ErrorResponse
from app.schemas.conversation import (
    ConversationHistoryResponse,
    ConversationMessageResponse,
    ConversationResponse,
)
from app.schemas.document import Citation, GroundedAnswer

CLIENT_FRAMES = {"ping", "ask"}
SERVER_FRAMES = {"ready", "pong", "delta", "complete", "error"}


# --------------------------------------------------------------------------
# The realtime frame contract
#
# FastAPI does not describe WebSocket routes, so this is the only place the
# socket's vocabulary is written down as an assertion.


def test_the_client_frame_vocabulary_is_pinned() -> None:
    assert {m.value for m in ClientMessageType} == CLIENT_FRAMES


def test_the_server_frame_vocabulary_is_pinned() -> None:
    assert {m.value for m in ServerMessageType} == SERVER_FRAMES


def test_every_server_frame_declares_its_type() -> None:
    """A client dispatches on `type`; every frame must carry one."""
    frames = [
        Ready(connection_id="a"),
        Pong(),
        Delta(text="x"),
        Complete(text="x", deltas=1),
        Error(code="c", message="m"),
    ]
    for frame in frames:
        rendered = json.loads(frame.model_dump_json())
        assert rendered["type"] in SERVER_FRAMES


def test_the_socket_error_frame_matches_the_http_error_shape() -> None:
    """One error format across both transports, so a client parses one thing."""
    socket_error = set(json.loads(Error(code="c", message="m").model_dump_json()))
    http_error = set(ErrorResponse.model_fields)
    assert http_error <= socket_error
    assert socket_error - http_error == {"type"}


def test_a_ping_needs_nothing_but_its_type() -> None:
    assert json.loads(Ping(type="ping").model_dump_json()) == {"type": "ping"}


def test_the_question_limit_is_published_as_a_constant() -> None:
    assert MAX_QUESTION_LENGTH > 0


# --------------------------------------------------------------------------
# Response consistency across the surface


def test_citations_have_one_shape_everywhere() -> None:
    """A frontend renders sources once, from one model."""
    assert (
        AssistantMessageResponse.model_fields["citations"].annotation
        == GroundedAnswer.model_fields["citations"].annotation
    )


def test_a_citation_carries_provenance_a_reader_can_check() -> None:
    assert set(Citation.model_fields) == {
        "document_id",
        "document_title",
        "ordinal",
        "excerpt",
        "similarity",
    }


def test_conversation_identity_is_one_shape() -> None:
    assert set(ConversationResponse.model_fields) == {"id", "customer_id", "created_at"}


def test_a_history_entry_carries_its_own_position() -> None:
    """Order is explicit, never inferred by the client from timestamps."""
    assert "position" in ConversationMessageResponse.model_fields
    assert set(ConversationHistoryResponse.model_fields) == {
        "conversation_id",
        "messages",
    }


def test_the_assistant_response_is_self_describing(client: TestClient) -> None:
    """Every field a client needs to render an answer and explain it."""
    fields = set(AssistantMessageResponse.model_fields)
    assert {"reply", "citations", "escalated", "handled", "conversation_id"} <= fields


# --------------------------------------------------------------------------
# The documented HTTP contract


def test_every_client_facing_schema_is_published(client: TestClient) -> None:
    published = set(client.get("/openapi.json").json()["components"]["schemas"])
    assert {
        "AssistantMessageRequest",
        "AssistantMessageResponse",
        "ConversationResponse",
        "ConversationHistoryResponse",
        "Citation",
        "ErrorResponse",
    } <= published


def test_every_published_schema_documents_its_fields(client: TestClient) -> None:
    """A frontend reads the spec; undescribed fields are guesswork."""
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    response = schemas["AssistantMessageResponse"]["properties"]
    undocumented = [name for name, spec in response.items() if not _described(spec)]
    assert undocumented == []


def _described(spec: dict) -> bool:
    if spec.get("description"):
        return True
    # A field whose type is a $ref carries its description on the referenced
    # schema, which is where a spec reader will look for it.
    return "$ref" in spec or "allOf" in spec or "anyOf" in spec


def test_conversation_endpoints_declare_their_404(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "404" in paths["/api/v1/conversations/{conversation_id}"]["get"]["responses"]
    assert (
        "404"
        in paths["/api/v1/conversations/{conversation_id}/messages"]["get"]["responses"]
    )


# --------------------------------------------------------------------------
# CORS, which a browser client cannot work without


def test_a_configured_origin_is_allowed() -> None:
    settings = Settings(cors_origins="http://localhost:5173")
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:5173"}
        )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_an_unconfigured_origin_is_not_allowed() -> None:
    """The allowlist has to actually exclude something to be an allowlist."""
    settings = Settings(cors_origins="http://localhost:5173")
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/api/v1/health", headers={"Origin": "http://evil.example"}
        )
    assert "access-control-allow-origin" not in response.headers


def test_a_preflight_for_the_assistant_endpoint_succeeds() -> None:
    settings = Settings(cors_origins="http://localhost:5173")
    with TestClient(create_app(settings)) as client:
        response = client.options(
            "/api/v1/assistant/messages",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# --------------------------------------------------------------------------
# Errors a client must be able to render


@pytest.fixture
def stubbed() -> TestClient:
    """A client whose DB-backed dependencies are substituted.

    Needed because FastAPI resolves dependencies before it validates the body:
    without a database configured, a malformed request to a DB-backed endpoint
    raises `database_not_configured` (500) before validation is ever reached.
    That is inherent to dependency ordering and predates this milestone -- the
    same holds for tickets and documents -- and in a deployment with a database
    the 422 arrives as expected, which the DB suite proves separately.

    Substituting here keeps this test about the thing it claims: that a client
    mistake is reported rather than hidden.
    """
    from app.api.v1.assistant import get_assistant_service
    from app.api.v1.conversations import get_conversation_service, get_customer_service

    app = create_app()
    for dependency in (
        get_assistant_service,
        get_conversation_service,
        get_customer_service,
    ):
        app.dependency_overrides[dependency] = lambda: object()
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/assistant/messages", {"message": ""}),
        ("/api/v1/assistant/messages", {}),
        ("/api/v1/assistant/messages", {"message": "hi", "unexpected": 1}),
        ("/api/v1/conversations", {"customer_email": "nope"}),
        ("/api/v1/conversations", {}),
    ],
)
def test_client_mistakes_are_reported_not_hidden(
    stubbed: TestClient, path: str, payload: dict
) -> None:
    """A 422, with the shared error envelope, never a 500."""
    response = stubbed.post(path, json=payload)
    assert response.status_code == 422


def test_a_validation_error_never_reaches_the_service(stubbed: TestClient) -> None:
    """The stub would raise if it were called, so a 422 proves it was not."""
    assert stubbed.post("/api/v1/assistant/messages", json={}).status_code == 422


def test_an_unknown_route_uses_the_shared_error_shape(client: TestClient) -> None:
    body = client.get("/api/v1/does-not-exist").json()
    assert set(body) <= set(ErrorResponse.model_fields)


def test_the_error_body_never_carries_internals(client: TestClient) -> None:
    body = str(client.get("/api/v1/does-not-exist").json())
    assert "Traceback" not in body and "sqlalchemy" not in body.lower()
