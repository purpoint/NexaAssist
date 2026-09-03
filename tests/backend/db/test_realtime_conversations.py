"""Realtime continuity against a real database.

Proves the socket and the HTTP endpoint write the same conversation the same
way -- a frontend that opens a conversation over HTTP and continues it over the
socket must see one history, not two.
"""

import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.v1.realtime import get_streaming_provider, reset_registry
from app.core.config import Settings
from app.llm.streaming import StaticStreamingProvider
from app.main import create_app
from app.realtime.conversations import SessionTurnRecorder, TurnRecorder
from app.services.errors import ConversationNotFoundError

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

CONVERSATIONS = "/api/v1/conversations"
WS = "/api/v1/ws"
ANSWER = "alpha beta"


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    reset_registry()


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(database_url=TEST_DATABASE_URL, embedding_provider="hashing")
    from app.db import health as health_module
    from app.db import session as session_module
    from app.db.engine import build_engine

    built = build_engine(settings)
    originals = (session_module.get_engine, health_module.get_engine)
    session_module.get_engine = lambda: built  # type: ignore[assignment]
    health_module.get_engine = lambda: built  # type: ignore[assignment]
    session_module.get_sessionmaker.cache_clear()

    app = create_app(settings)
    app.dependency_overrides[get_streaming_provider] = lambda: StaticStreamingProvider(
        ANSWER
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        session_module.get_engine, health_module.get_engine = originals  # type: ignore[assignment]
        session_module.get_sessionmaker.cache_clear()


def open_conversation(client: TestClient) -> str:
    response = client.post(CONVERSATIONS, json={"customer_email": "p@example.com"})
    assert response.status_code == 201
    return response.json()["id"]


def ask(socket: object, question: str, conversation_id: str | None = None) -> list[dict]:
    """Send one ask and collect frames up to the terminating one."""
    payload: dict = {"type": "ask", "question": question}
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    socket.send_text(json.dumps(payload))

    frames: list[dict] = []
    while True:
        frame = socket.receive_json()
        frames.append(frame)
        if frame["type"] in ("complete", "error"):
            return frames


def test_the_shipped_recorder_satisfies_the_protocol() -> None:
    assert isinstance(SessionTurnRecorder(), TurnRecorder)


def test_a_realtime_exchange_is_recorded(client: TestClient) -> None:
    conversation = open_conversation(client)

    with client.websocket_connect(WS) as socket:
        socket.receive_json()  # ready
        frames = ask(socket, "why is it slow?", conversation)

    assert frames[-1]["type"] == "complete"
    assert frames[-1]["conversation_id"] == conversation

    history = client.get(f"{CONVERSATIONS}/{conversation}/messages").json()
    assert [m["role"] for m in history["messages"]] == ["customer", "assistant"]
    assert history["messages"][0]["content"] == "why is it slow?"
    assert history["messages"][1]["content"] == ANSWER


def test_http_and_socket_share_one_conversation(client: TestClient) -> None:
    """The whole point: one history, not two."""
    conversation = open_conversation(client)

    with client.websocket_connect(WS) as socket:
        socket.receive_json()
        ask(socket, "first over the socket", conversation)

    client.post(
        "/api/v1/assistant/messages",
        json={"message": "then over http", "conversation_id": conversation},
    )

    history = client.get(f"{CONVERSATIONS}/{conversation}/messages").json()
    contents = [m["content"] for m in history["messages"]]
    positions = [m["position"] for m in history["messages"]]
    assert contents[0] == "first over the socket"
    assert "then over http" in contents
    assert positions == sorted(positions), "one ordered history"
    assert len(contents) == 4


def test_an_unknown_conversation_is_reported_on_the_socket(client: TestClient) -> None:
    with client.websocket_connect(WS) as socket:
        socket.receive_json()
        frames = ask(socket, "why?", str(uuid.uuid4()))

    assert [f["type"] for f in frames] == ["error"]
    assert frames[0]["code"] == ConversationNotFoundError.code


def test_the_socket_survives_an_unknown_conversation(client: TestClient) -> None:
    """A client mistake on one message must not end the session."""
    conversation = open_conversation(client)
    with client.websocket_connect(WS) as socket:
        socket.receive_json()
        ask(socket, "why?", str(uuid.uuid4()))
        frames = ask(socket, "and now?", conversation)

    assert frames[-1]["type"] == "complete"


def test_an_ask_without_a_conversation_records_nothing(client: TestClient) -> None:
    conversation = open_conversation(client)
    with client.websocket_connect(WS) as socket:
        socket.receive_json()
        frames = ask(socket, "no conversation here")

    assert frames[-1]["conversation_id"] is None
    history = client.get(f"{CONVERSATIONS}/{conversation}/messages").json()
    assert history["messages"] == []


def test_several_turns_accumulate_in_order(client: TestClient) -> None:
    conversation = open_conversation(client)
    with client.websocket_connect(WS) as socket:
        socket.receive_json()
        for question in ("one", "two", "three"):
            ask(socket, question, conversation)

    history = client.get(f"{CONVERSATIONS}/{conversation}/messages").json()
    assert [m["content"] for m in history["messages"]][::2] == ["one", "two", "three"]
    assert len(history["messages"]) == 6


def test_a_conversation_can_be_fetched_before_it_is_rendered(
    client: TestClient,
) -> None:
    conversation = open_conversation(client)
    response = client.get(f"{CONVERSATIONS}/{conversation}")
    assert response.status_code == 200
    assert response.json()["id"] == conversation


def test_fetching_an_unknown_conversation_is_a_404(client: TestClient) -> None:
    response = client.get(f"{CONVERSATIONS}/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["code"] == ConversationNotFoundError.code
