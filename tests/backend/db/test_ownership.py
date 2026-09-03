"""Resource ownership end to end, with two authenticated subjects.

The point of these is cross-subject access: one subject creates a resource and
the other must be unable to tell it exists.
"""

import logging
import uuid
from collections.abc import Iterator

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app.agent.loop import AgentDecision
from app.api.v1.identity import API_KEY_HEADER
from app.core.config import Settings
from app.llm.base import LLMConfig
from app.llm.providers.static_provider import StaticLLMProvider
from app.llm.streaming import StaticStreamingProvider
from app.main import create_app
from app.rag.embeddings import HashingEmbeddingProvider
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.answer import GroundedModelAnswer

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

WEB_KEY = "web-key-0123456789abcdef"
WORKER_KEY = "worker-key-fedcba987654321"
KEYS = f"web-app:{WEB_KEY},worker:{WORKER_KEY}"

CONVERSATIONS = "/api/v1/conversations"
TICKETS = "/api/v1/tickets"
MESSAGES = "/api/v1/assistant/messages"

WEB = {API_KEY_HEADER: WEB_KEY}
WORKER = {API_KEY_HEADER: WORKER_KEY}

ANALYSIS = IntentAnalysis(
    intent=IntentCategory.BILLING, confidence=0.95, reason="fixture"
)


def canned() -> StaticLLMProvider:
    return StaticLLMProvider(
        LLMConfig(provider="static", model="static-model"),
        canned={
            IntentAnalysis: ANALYSIS,
            AgentDecision: AgentDecision(action="answer", answer="Under Billing."),
            GroundedModelAnswer: GroundedModelAnswer(
                answered=True, answer="Under Billing.", cited_sources=[]
            ),
        },
    )


def build(scoped: bool) -> Iterator[TestClient]:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        embedding_provider="hashing",
        auth_provider="api_key",
        auth_api_keys=KEYS,
        authz_provider="subject" if scoped else "open",
    )
    from app.api.v1.realtime import get_streaming_provider
    from app.auth.factory import get_authenticator, get_authorizer
    from app.auth.factory import build_authenticator, build_authorizer
    from app.db import health as health_module
    from app.db import session as session_module
    from app.db.engine import build_engine
    from app.llm.factory import get_llm_provider
    from app.rag.factory import get_embedding_provider

    built = build_engine(settings)
    originals = (session_module.get_engine, health_module.get_engine)
    session_module.get_engine = lambda: built  # type: ignore[assignment]
    health_module.get_engine = lambda: built  # type: ignore[assignment]
    session_module.get_sessionmaker.cache_clear()

    app = create_app(settings)
    app.dependency_overrides[get_embedding_provider] = HashingEmbeddingProvider
    app.dependency_overrides[get_llm_provider] = canned
    app.dependency_overrides[get_authenticator] = lambda: build_authenticator(settings)
    app.dependency_overrides[get_authorizer] = lambda: build_authorizer(settings)
    # Without this the socket resolves the *configured* provider, which in a
    # developer environment is Groq with a real key -- a real, billable call
    # from the test suite.
    app.dependency_overrides[get_streaming_provider] = lambda: StaticStreamingProvider(
        "Under Billing."
    )
    try:
        with TestClient(app) as client:
            yield client
    finally:
        session_module.get_engine, health_module.get_engine = originals  # type: ignore[assignment]
        session_module.get_sessionmaker.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Ownership enforced."""
    yield from build(scoped=True)


@pytest.fixture
def unscoped() -> Iterator[TestClient]:
    """Authentication on, ownership off -- the pre-M19-C2 behaviour."""
    yield from build(scoped=False)


def open_conversation(client: TestClient, headers: dict, email: str) -> str:
    response = client.post(
        CONVERSATIONS, json={"customer_email": email}, headers=headers
    )
    assert response.status_code == 201
    return response.json()["id"]


def realtime_ticket(client: TestClient, headers: dict) -> str:
    """Trade an authenticated request for a handshake ticket."""
    response = client.post("/api/v1/ws/ticket", headers=headers)
    assert response.status_code == 200
    return response.json()["ticket"]


def raise_ticket(client: TestClient, headers: dict, email: str) -> str:
    response = client.post(
        TICKETS,
        json={"customer_email": email, "subject": "Invoice", "body": "Where is it?"},
        headers=headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


# --------------------------------------------------------------------------
# Conversations


def test_an_owner_can_read_its_own_conversation(client: TestClient) -> None:
    conversation = open_conversation(client, WEB, "a@example.com")
    assert client.get(f"{CONVERSATIONS}/{conversation}", headers=WEB).status_code == 200
    assert (
        client.get(f"{CONVERSATIONS}/{conversation}/messages", headers=WEB).status_code
        == 200
    )


def test_another_subject_cannot_tell_it_exists(client: TestClient) -> None:
    """404, not 403: a 403 would confirm the conversation is real."""
    conversation = open_conversation(client, WEB, "a@example.com")

    refused = client.get(f"{CONVERSATIONS}/{conversation}", headers=WORKER)
    missing = client.get(f"{CONVERSATIONS}/{uuid.uuid4()}", headers=WORKER)

    assert refused.status_code == missing.status_code == 404
    assert refused.json()["code"] == missing.json()["code"] == "conversation_not_found"
    assert refused.json()["message"] == missing.json()["message"]


def test_another_subject_cannot_read_the_history(client: TestClient) -> None:
    conversation = open_conversation(client, WEB, "a@example.com")
    client.post(
        MESSAGES,
        json={"message": "mine", "conversation_id": conversation},
        headers=WEB,
    )
    response = client.get(f"{CONVERSATIONS}/{conversation}/messages", headers=WORKER)
    assert response.status_code == 404
    assert "mine" not in response.text


def test_another_subject_cannot_append_to_it(client: TestClient) -> None:
    conversation = open_conversation(client, WEB, "a@example.com")
    response = client.post(
        MESSAGES,
        json={"message": "not yours", "conversation_id": conversation},
        headers=WORKER,
    )
    assert response.status_code == 404

    history = client.get(f"{CONVERSATIONS}/{conversation}/messages", headers=WEB).json()
    assert history["messages"] == [], "nothing was written"


def test_the_owner_is_stamped_from_the_identity(client: TestClient) -> None:
    """Identity propagation: the creating subject becomes the owner."""
    web = open_conversation(client, WEB, "a@example.com")
    worker = open_conversation(client, WORKER, "b@example.com")
    assert client.get(f"{CONVERSATIONS}/{web}", headers=WEB).status_code == 200
    assert client.get(f"{CONVERSATIONS}/{worker}", headers=WORKER).status_code == 200
    assert client.get(f"{CONVERSATIONS}/{worker}", headers=WEB).status_code == 404


# --------------------------------------------------------------------------
# Tickets


def test_an_owner_can_read_its_own_ticket(client: TestClient) -> None:
    ticket = raise_ticket(client, WEB, "a@example.com")
    assert client.get(f"{TICKETS}/{ticket}", headers=WEB).status_code == 200


def test_another_subject_cannot_read_the_ticket(client: TestClient) -> None:
    ticket = raise_ticket(client, WEB, "a@example.com")
    refused = client.get(f"{TICKETS}/{ticket}", headers=WORKER)
    missing = client.get(f"{TICKETS}/{uuid.uuid4()}", headers=WORKER)
    assert refused.status_code == missing.status_code == 404
    assert refused.json()["code"] == missing.json()["code"] == "ticket_not_found"


def test_a_listing_shows_only_the_callers_tickets(client: TestClient) -> None:
    raise_ticket(client, WEB, "a@example.com")
    raise_ticket(client, WEB, "a2@example.com")
    raise_ticket(client, WORKER, "b@example.com")

    web = client.get(TICKETS, headers=WEB).json()
    worker = client.get(TICKETS, headers=WORKER).json()
    assert len(web["items"]) == 2
    assert len(worker["items"]) == 1


def test_a_listing_never_reveals_another_subjects_content(client: TestClient) -> None:
    raise_ticket(client, WEB, "secret-person@example.com")
    body = client.get(TICKETS, headers=WORKER).text
    assert "secret-person" not in body


# --------------------------------------------------------------------------
# Unscoped deployments are unchanged


def test_without_scoping_both_subjects_share_everything(unscoped: TestClient) -> None:
    """The pre-M19-C2 contract, still intact."""
    conversation = open_conversation(unscoped, WEB, "a@example.com")
    assert (
        unscoped.get(f"{CONVERSATIONS}/{conversation}", headers=WORKER).status_code
        == 200
    )
    ticket = raise_ticket(unscoped, WEB, "a@example.com")
    assert unscoped.get(f"{TICKETS}/{ticket}", headers=WORKER).status_code == 200
    assert len(unscoped.get(TICKETS, headers=WORKER).json()["items"]) == 1


def test_without_scoping_no_owner_is_recorded(unscoped: TestClient) -> None:
    """So enabling scoping later cannot hand old rows to a subject."""
    conversation = open_conversation(unscoped, WEB, "a@example.com")
    # Re-read under scoping: an unowned row is refused, which is only
    # observable if no owner was stamped.
    for client in build(scoped=True):
        assert client.get(f"{CONVERSATIONS}/{conversation}", headers=WEB).status_code == 404


# --------------------------------------------------------------------------
# Refusals are logged, not leaked


def test_a_refusal_is_logged_with_the_subject_not_the_content(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    conversation = open_conversation(client, WEB, "a@example.com")
    with caplog.at_level(logging.WARNING, logger="app.services.conversation"):
        client.get(f"{CONVERSATIONS}/{conversation}", headers=WORKER)
    assert "subject=worker" in caplog.text
    assert "a@example.com" not in caplog.text


def test_a_refusal_body_carries_no_internals(client: TestClient) -> None:
    conversation = open_conversation(client, WEB, "a@example.com")
    body = client.get(f"{CONVERSATIONS}/{conversation}", headers=WORKER).text
    assert "owner" not in body.lower()
    assert "postgresql" not in body and "Traceback" not in body


# --------------------------------------------------------------------------
# The socket fails closed under scoping


def test_a_handshake_without_a_ticket_is_refused(client: TestClient) -> None:
    """The socket used to refuse to *record*; now it refuses to connect."""
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.receive_json()
    assert caught.value.code == 1008


def test_a_handshake_with_a_bad_ticket_is_refused(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect("/api/v1/ws?ticket=not-a-real-ticket") as socket:
            socket.receive_json()
    assert caught.value.code == 1008


def test_a_ticketed_socket_records_under_its_own_ownership(
    client: TestClient,
) -> None:
    """The limitation tickets were built to remove."""
    conversation = open_conversation(client, WEB, "a@example.com")
    ticket = realtime_ticket(client, WEB)

    with client.websocket_connect(f"/api/v1/ws?ticket={ticket}") as socket:
        socket.receive_json()
        socket.send_text(
            '{"type": "ask", "question": "why?", "conversation_id": "%s"}'
            % conversation
        )
        while True:
            frame = socket.receive_json()
            if frame["type"] in ("complete", "error"):
                break

    assert frame["type"] == "complete"
    history = client.get(f"{CONVERSATIONS}/{conversation}/messages", headers=WEB).json()
    assert [m["role"] for m in history["messages"]] == ["customer", "assistant"]


def test_a_ticket_cannot_reach_another_subjects_conversation(
    client: TestClient,
) -> None:
    """Ownership over the socket is the same 404 it is over HTTP."""
    conversation = open_conversation(client, WEB, "a@example.com")
    ticket = realtime_ticket(client, WORKER)

    with client.websocket_connect(f"/api/v1/ws?ticket={ticket}") as socket:
        socket.receive_json()
        socket.send_text(
            '{"type": "ask", "question": "why?", "conversation_id": "%s"}'
            % conversation
        )
        frame = socket.receive_json()

    assert frame["type"] == "error"
    assert frame["code"] == "conversation_not_found"
    history = client.get(f"{CONVERSATIONS}/{conversation}/messages", headers=WEB).json()
    assert history["messages"] == []


def test_a_ticket_is_spent_by_the_first_handshake(client: TestClient) -> None:
    """Single use is half of what makes a ticket in a URL acceptable."""
    ticket = realtime_ticket(client, WEB)

    with client.websocket_connect(f"/api/v1/ws?ticket={ticket}") as socket:
        socket.receive_json()

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/ws?ticket={ticket}") as socket:
            socket.receive_json()


def test_minting_a_ticket_needs_the_key(client: TestClient) -> None:
    assert client.post("/api/v1/ws/ticket").status_code == 401
    assert client.post("/api/v1/ws/ticket", headers=WEB).status_code == 200


def test_the_ticket_response_carries_no_credential(client: TestClient) -> None:
    body = client.post("/api/v1/ws/ticket", headers=WEB).json()
    assert set(body) == {"ticket", "expires_in_seconds"}
    assert WEB_KEY not in body["ticket"]
    assert body["expires_in_seconds"] > 0


def test_the_socket_still_answers_without_a_conversation(client: TestClient) -> None:
    ticket = realtime_ticket(client, WEB)
    with client.websocket_connect(f"/api/v1/ws?ticket={ticket}") as socket:
        socket.receive_json()
        socket.send_text('{"type": "ask", "question": "why?"}')
        kinds = []
        while True:
            frame = socket.receive_json()
            kinds.append(frame["type"])
            if frame["type"] in ("complete", "error"):
                break
    assert kinds[-1] == "complete"
