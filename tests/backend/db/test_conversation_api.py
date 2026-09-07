"""Conversation endpoints and assistant continuity, against a real database."""

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.agent.loop import AgentDecision
from app.core.config import Settings
from app.llm.base import LLMConfig
from app.llm.providers.static_provider import StaticLLMProvider
from app.main import create_app
from app.models import Customer
from app.models.conversation import MessageRole
from app.rag.embeddings import HashingEmbeddingProvider
from app.schemas.document import Citation
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.answer import GroundedModelAnswer
from app.services.conversation import ConversationService

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
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    """A session for the tests that exercise the service directly.

    The endpoint tests below drive everything through the client; these few
    need to write a turn with sources attached, which no endpoint does on its
    own -- the assistant does it as part of answering.
    """
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


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


async def a_customer(session: AsyncSession) -> uuid.UUID:
    """A customer row to hang a conversation from, as the endpoint would."""
    customer = Customer(email=f"person-{uuid.uuid4().hex[:8]}@example.com")
    session.add(customer)
    await session.flush()
    await session.commit()
    return customer.id


# --------------------------------------------------------------------------
# Sources survive the reload
#
# Provenance used to live only in the live response: an answer cited a
# document while you watched it and cited nothing once the page was
# reloaded. The same answer making two different claims is worse than
# making none.


@pytest.mark.anyio
async def test_an_answers_sources_are_recorded_with_it(session: AsyncSession) -> None:
    conversation = await ConversationService(session).start(await a_customer(session))
    cited = Citation(
        document_id=uuid.uuid4(),
        document_title="Shipping and delivery",
        ordinal=0,
        excerpt="Standard shipping takes 3 to 5 business days.",
        similarity=0.86,
    )

    await ConversationService(session).append(
        conversation.id,
        role=MessageRole.ASSISTANT,
        content="Three to five business days.",
        citations=[cited],
    )

    history = await ConversationService(session).history(conversation.id)
    stored = history[-1].citations
    assert len(stored) == 1
    assert stored[0]["document_title"] == "Shipping and delivery"
    assert stored[0]["similarity"] == 0.86


@pytest.mark.anyio
async def test_a_turn_with_no_sources_records_none(session: AsyncSession) -> None:
    """Empty, never null: a caller reads a list either way."""
    conversation = await ConversationService(session).start(await a_customer(session))

    await ConversationService(session).append(
        conversation.id, role=MessageRole.CUSTOMER, content="How long is shipping?"
    )

    history = await ConversationService(session).history(conversation.id)
    assert history[-1].citations == []


@pytest.mark.anyio
async def test_recorded_sources_do_not_follow_the_document(
    session: AsyncSession,
) -> None:
    """A snapshot, which is the reason for storing them rather than resolving.

    The excerpt describes what an answer was based on at the time. Editing
    the document afterwards must not rewrite what an old answer claims.
    """
    conversation = await ConversationService(session).start(await a_customer(session))
    document_id = uuid.uuid4()
    await ConversationService(session).append(
        conversation.id,
        role=MessageRole.ASSISTANT,
        content="Three to five business days.",
        citations=[
            Citation(
                document_id=document_id,
                document_title="Shipping",
                ordinal=0,
                excerpt="Three to five business days.",
                similarity=0.9,
            )
        ],
    )

    history = await ConversationService(session).history(conversation.id)
    # The row holds the text, not a pointer to whatever the document says now.
    assert history[-1].citations[0]["excerpt"] == "Three to five business days."
    assert history[-1].citations[0]["document_id"] == str(document_id)


@pytest.mark.anyio
async def test_the_history_endpoint_returns_the_sources(
    client: TestClient, session: AsyncSession
) -> None:
    conversation = await ConversationService(session).start(await a_customer(session))
    await ConversationService(session).append(
        conversation.id,
        role=MessageRole.ASSISTANT,
        content="Three to five business days.",
        citations=[
            Citation(
                document_id=uuid.uuid4(),
                document_title="Shipping",
                ordinal=1,
                excerpt="Three to five.",
                similarity=0.8,
            )
        ],
    )

    body = client.get(f"/api/v1/conversations/{conversation.id}/messages").json()

    assistant = [m for m in body["messages"] if m["role"] == "assistant"][-1]
    assert assistant["citations"][0]["document_title"] == "Shipping"
    assert assistant["citations"][0]["ordinal"] == 1
    customer_turns = [m for m in body["messages"] if m["role"] == "customer"]
    assert all(m["citations"] == [] for m in customer_turns)
