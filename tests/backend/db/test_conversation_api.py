"""Conversation endpoints and assistant continuity, against a real database."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agent.loop import AgentDecision
from app.core.config import Settings
from app.llm.base import LLMConfig
from app.llm.providers.static_provider import StaticLLMProvider
from app.main import create_app
from app.models import Customer
from app.rag.embeddings import HashingEmbeddingProvider
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.answer import GroundedModelAnswer

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

CONVERSATIONS = "/api/v1/conversations"
MESSAGES = "/api/v1/assistant/messages"
TICKETS = "/api/v1/tickets"
EMAIL = "person@example.com"

ANALYSIS = IntentAnalysis(
    intent=IntentCategory.BILLING, confidence=0.95, reason="fixture"
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(database_url=TEST_DATABASE_URL, embedding_provider="hashing")
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

    provider = StaticLLMProvider(
        LLMConfig(provider="static", model="static-model"),
        canned={
            IntentAnalysis: ANALYSIS,
            AgentDecision: AgentDecision(action="answer", answer="Under Billing."),
            GroundedModelAnswer: GroundedModelAnswer(
                answered=True, answer="Under Billing.", cited_sources=[]
            ),
        },
    )
    app = create_app(settings)
    app.dependency_overrides[get_embedding_provider] = HashingEmbeddingProvider
    app.dependency_overrides[get_llm_provider] = lambda: provider
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        session_module.get_engine, health_module.get_engine = originals  # type: ignore[assignment]
        session_module.get_sessionmaker.cache_clear()


def open_conversation(client: TestClient, email: str = EMAIL) -> dict:
    response = client.post(CONVERSATIONS, json={"customer_email": email})
    assert response.status_code == 201
    return response.json()


# --------------------------------------------------------------------------
# Opening


def test_a_conversation_is_opened_with_its_customer(client: TestClient) -> None:
    body = open_conversation(client)
    assert uuid.UUID(body["id"])
    assert uuid.UUID(body["customer_id"])
    assert body["created_at"]


def test_a_returning_customer_is_not_duplicated(client: TestClient) -> None:
    first = open_conversation(client)
    second = open_conversation(client)
    assert first["customer_id"] == second["customer_id"]
    assert first["id"] != second["id"]


def test_the_address_is_normalised(client: TestClient) -> None:
    lower = open_conversation(client, "person@example.com")
    upper = open_conversation(client, "  PERSON@example.com  ")
    assert lower["customer_id"] == upper["customer_id"]


def test_conversations_and_tickets_agree_on_the_customer(client: TestClient) -> None:
    """Two get-or-create paths exist; they must not drift apart.

    TicketService keeps its own private copy from M4, and CustomerService is
    the one M17 added. If they ever disagree about an address, this fails.
    """
    ticket = client.post(
        TICKETS,
        json={"customer_email": EMAIL, "subject": "Invoice", "body": "Where is it?"},
    )
    assert ticket.status_code == 201
    conversation = open_conversation(client)
    assert ticket.json()["customer_id"] == conversation["customer_id"]


@pytest.mark.parametrize(
    "payload", [{}, {"customer_email": "not-an-email"}, {"customer_email": EMAIL, "x": 1}]
)
def test_an_invalid_open_request_is_rejected(client: TestClient, payload: dict) -> None:
    assert client.post(CONVERSATIONS, json=payload).status_code == 422


@pytest.mark.anyio
async def test_only_one_customer_row_is_created(
    client: TestClient, engine: AsyncEngine
) -> None:
    open_conversation(client)
    open_conversation(client)
    async with engine.connect() as connection:
        rows = (await connection.scalars(select(Customer.id))).all()
    assert len(rows) == 1


# --------------------------------------------------------------------------
# Continuity


def test_an_exchange_is_recorded_against_the_conversation(client: TestClient) -> None:
    conversation = open_conversation(client)
    answered = client.post(
        MESSAGES,
        json={"message": "Where are my invoices?", "conversation_id": conversation["id"]},
    )
    assert answered.status_code == 200
    assert answered.json()["conversation_id"] == conversation["id"]

    history = client.get(f"{CONVERSATIONS}/{conversation['id']}/messages").json()
    assert [m["role"] for m in history["messages"]] == ["customer", "assistant"]
    assert history["messages"][0]["content"] == "Where are my invoices?"
    assert history["messages"][1]["content"] == answered.json()["reply"]


def test_turns_are_positioned_in_order(client: TestClient) -> None:
    conversation = open_conversation(client)
    for text in ("first", "second", "third"):
        client.post(
            MESSAGES, json={"message": text, "conversation_id": conversation["id"]}
        )
    history = client.get(f"{CONVERSATIONS}/{conversation['id']}/messages").json()
    positions = [m["position"] for m in history["messages"]]
    assert positions == sorted(positions)
    assert len(positions) == 6


def test_a_message_without_a_conversation_still_answers(client: TestClient) -> None:
    """A caller with no conversation is not forced to open one."""
    body = client.post(MESSAGES, json={"message": "Where are my invoices?"}).json()
    assert body["reply"]
    assert body["conversation_id"] is None


def test_an_unknown_conversation_is_a_404(client: TestClient) -> None:
    response = client.post(
        MESSAGES, json={"message": "hello", "conversation_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "conversation_not_found"


def test_reading_an_unknown_conversation_is_a_404(client: TestClient) -> None:
    response = client.get(f"{CONVERSATIONS}/{uuid.uuid4()}/messages")
    assert response.status_code == 404


def test_history_can_be_limited_to_the_most_recent(client: TestClient) -> None:
    conversation = open_conversation(client)
    for text in ("first", "second", "third"):
        client.post(
            MESSAGES, json={"message": text, "conversation_id": conversation["id"]}
        )
    limited = client.get(
        f"{CONVERSATIONS}/{conversation['id']}/messages", params={"limit": 2}
    ).json()
    assert len(limited["messages"]) == 2
    positions = [m["position"] for m in limited["messages"]]
    assert positions == sorted(positions), "still oldest-first"


def test_an_empty_conversation_has_no_turns(client: TestClient) -> None:
    conversation = open_conversation(client)
    history = client.get(f"{CONVERSATIONS}/{conversation['id']}/messages").json()
    assert history["messages"] == []
    assert history["conversation_id"] == conversation["id"]


@pytest.mark.parametrize("limit", [0, 501, "many"])
def test_an_invalid_limit_is_rejected(client: TestClient, limit: object) -> None:
    conversation = open_conversation(client)
    response = client.get(
        f"{CONVERSATIONS}/{conversation['id']}/messages", params={"limit": limit}
    )
    assert response.status_code == 422


def test_two_conversations_do_not_mix(client: TestClient) -> None:
    first = open_conversation(client)
    second = open_conversation(client, "other@example.com")
    client.post(MESSAGES, json={"message": "mine", "conversation_id": first["id"]})

    other = client.get(f"{CONVERSATIONS}/{second['id']}/messages").json()
    assert other["messages"] == []
