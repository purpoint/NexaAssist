"""The WebSocket transport: wire contract, capacity, and failure modes.

FastAPI does not describe WebSocket routes in OpenAPI, so these tests are the
only executable statement of what the socket accepts and returns. They are
written against the frames rather than the implementation for that reason.
"""

import json

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.realtime import (
    CLOSE_AT_CAPACITY,
    CLOSE_TOO_LARGE,
    get_registry,
    reset_registry,
)
from app.core.config import Settings, get_settings
from app.main import create_app
from app.realtime.connection import Connection, ConnectionRegistry
from app.realtime.envelope import (
    MAX_MESSAGE_BYTES,
    Error,
    Ping,
    Pong,
    Ready,
    ServerMessageType,
    parse_client_message,
)
from app.realtime.errors import RealtimeCapacityError


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    """The registry is process-wide, so tests must not inherit each other's."""
    reset_registry()


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


# --------------------------------------------------------------------------
# The wire contract


def test_a_ping_frame_parses() -> None:
    assert parse_client_message('{"type": "ping"}') == Ping(type="ping")


def test_an_unknown_type_is_refused() -> None:
    """A permissive socket is an undocumented API."""
    with pytest.raises(ValidationError):
        parse_client_message('{"type": "shutdown"}')


def test_a_frame_with_no_type_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_client_message('{"hello": "there"}')


def test_extra_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        parse_client_message('{"type": "ping", "extra": 1}')


def test_malformed_json_is_refused() -> None:
    with pytest.raises(ValidationError):
        parse_client_message("not json at all")


def test_server_frames_carry_their_type() -> None:
    assert json.loads(Pong().model_dump_json())["type"] == "pong"
    assert json.loads(Ready(connection_id="a").model_dump_json())["type"] == "ready"
    assert (
        json.loads(Error(code="c", message="m").model_dump_json())["type"] == "error"
    )


def test_the_error_frame_matches_the_http_error_shape() -> None:
    """One error format across both transports."""
    body = json.loads(Error(code="realtime_protocol_error", message="No.").model_dump_json())
    assert {"code", "message"} <= set(body)


def test_the_frame_ceiling_is_generous_but_bounded() -> None:
    assert 1024 < MAX_MESSAGE_BYTES <= 1_048_576


# --------------------------------------------------------------------------
# The registry


def test_connections_are_registered_and_removed() -> None:
    registry = ConnectionRegistry(max_connections=2)
    first = registry.add(FakeSocket())
    assert len(registry) == 1
    assert registry.get(first.id) is first

    registry.remove(first)
    assert len(registry) == 0
    assert registry.get(first.id) is None


def test_removing_twice_is_not_an_error() -> None:
    """A disconnect can be noticed in more than one place."""
    registry = ConnectionRegistry()
    connection = registry.add(FakeSocket())
    registry.remove(connection)
    registry.remove(connection)
    assert len(registry) == 0


def test_the_ceiling_is_enforced() -> None:
    registry = ConnectionRegistry(max_connections=1)
    registry.add(FakeSocket())
    with pytest.raises(RealtimeCapacityError):
        registry.add(FakeSocket())


def test_capacity_is_freed_when_a_connection_leaves() -> None:
    registry = ConnectionRegistry(max_connections=1)
    first = registry.add(FakeSocket())
    registry.remove(first)
    registry.add(FakeSocket())
    assert len(registry) == 1


def test_a_registry_must_admit_at_least_one() -> None:
    with pytest.raises(ValueError):
        ConnectionRegistry(max_connections=0)


def test_connection_ids_are_unique() -> None:
    registry = ConnectionRegistry()
    assert registry.add(FakeSocket()).id != registry.add(FakeSocket()).id


@pytest.mark.anyio
async def test_a_connection_only_sends_envelope_models() -> None:
    socket = FakeSocket()
    connection = Connection(socket=socket)
    await connection.send(Pong())
    assert json.loads(socket.sent[0])["type"] == "pong"

    with pytest.raises(TypeError):
        await connection.send({"type": "pong"})


# --------------------------------------------------------------------------
# The endpoint


def test_the_socket_greets_then_answers_a_ping(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/ws") as socket:
        ready = socket.receive_json()
        assert ready["type"] == ServerMessageType.READY
        assert ready["protocol_version"] == 1
        assert ready["connection_id"]

        socket.send_text('{"type": "ping"}')
        assert socket.receive_json()["type"] == ServerMessageType.PONG


def test_a_malformed_frame_is_answered_without_dropping_the_socket(
    client: TestClient,
) -> None:
    """A client bug on one message is not a reason to end the session."""
    with client.websocket_connect("/api/v1/ws") as socket:
        socket.receive_json()

        socket.send_text('{"type": "definitely-not-a-thing"}')
        error = socket.receive_json()
        assert error["type"] == ServerMessageType.ERROR
        assert error["code"] == "realtime_protocol_error"

        socket.send_text('{"type": "ping"}')
        assert socket.receive_json()["type"] == ServerMessageType.PONG


def test_the_error_frame_does_not_echo_what_was_sent(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/ws") as socket:
        socket.receive_json()
        socket.send_text('{"type": "leak-me-account-12345"}')
        error = socket.receive_json()
    assert "leak-me-account-12345" not in json.dumps(error)


def test_an_oversized_frame_closes_the_socket() -> None:
    settings = Settings(realtime_max_message_bytes=1024)
    app = create_app(settings)
    # create_app does not steer request-time dependencies -- inherited
    # behaviour, documented in docs/architecture.md -- so the override is what
    # actually puts this setting in front of the endpoint.
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.receive_json()
            socket.send_text(json.dumps({"type": "ping", "pad": "x" * 2000}))
            with pytest.raises(WebSocketDisconnect) as caught:
                socket.receive_json()
    assert caught.value.code == CLOSE_TOO_LARGE


def test_the_socket_is_refused_once_capacity_is_reached() -> None:
    settings = Settings(realtime_max_connections=1)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    # One instance, returned every time: a ceiling only counts if every
    # connection is counted against the same registry. This is exactly what
    # the production dependency caches for.
    shared = ConnectionRegistry(max_connections=1)
    app.dependency_overrides[get_registry] = lambda: shared
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws") as first:
            first.receive_json()
            with pytest.raises(WebSocketDisconnect) as caught:
                with client.websocket_connect("/api/v1/ws") as second:
                    second.receive_json()
    assert caught.value.code == CLOSE_AT_CAPACITY


def test_a_disconnect_frees_the_slot(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/ws") as socket:
        socket.receive_json()
        assert len(get_registry()) == 1
    assert len(get_registry()) == 0


def test_the_websocket_route_is_absent_from_openapi(client: TestClient) -> None:
    """Pinning a fact, not a wish.

    FastAPI describes HTTP operations only, so mounting this endpoint leaves
    the schema untouched -- which is why the wire contract has to be stated in
    code and tests instead.
    """
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/ws" not in paths
