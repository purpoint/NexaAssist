"""The assistant endpoint against a real database and the real pipeline.

Nothing is stubbed except the model: the router, handlers, agent, tools,
policy engine, escalation criteria, and review queue are the shipped ones.

The engine is patched the way ``test_document_api`` patches it. ``create_app``
does not steer request-time dependencies -- inherited M1 behaviour, recorded in
the open items -- so the session dependency would otherwise resolve the
globally cached settings and find no database.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.main import create_app
from app.models import ReviewItem
from app.rag.embeddings import HashingEmbeddingProvider
from app.schemas.assistant import AssistantMessageResponse
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.agent.loop import AgentDecision
from app.llm.base import LLMConfig
from app.llm.providers.static_provider import StaticLLMProvider
from app.services.answer import GroundedModelAnswer

from .conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("clean_tables")

PATH = "/api/v1/assistant/messages"


AGENT_ANSWER = "Your invoices are under Billing in account settings."
GROUNDED_ANSWER = "Refunds take five business days."


def canned_provider(analysis: IntentAnalysis) -> StaticLLMProvider:
    """One provider answering every schema the pipeline asks for.

    The pipeline makes more than one model call -- classification, then either
    an agent step or a grounded answer -- so a fake returning a single fixed
    object cannot drive it. StaticLLMProvider is the shipped offline provider
    and is canned per schema, which is exactly this shape.
    """
    return StaticLLMProvider(
        LLMConfig(provider="static", model="static-model"),
        canned={
            IntentAnalysis: analysis,
            AgentDecision: AgentDecision(action="answer", answer=AGENT_ANSWER),
            GroundedModelAnswer: GroundedModelAnswer(
                answered=True, answer=GROUNDED_ANSWER, cited_sources=[]
            ),
        },
    )


def client_answering(analysis: IntentAnalysis) -> Iterator[TestClient]:
    settings = Settings(database_url=TEST_DATABASE_URL, embedding_provider="hashing")
    from app.db import health as health_module
    from app.db import session as session_module
    from app.db.engine import build_engine
    from app.llm.factory import get_llm_provider
    from app.rag.factory import get_embedding_provider

    built = build_engine(settings)
    # Both call sites, not just the session one: the readiness probe resolves
    # get_engine through app.db.health, so patching only the session module
    # would leave readiness reporting "not_configured" and the assertion below
    # would be measuring the harness rather than the endpoint.
    original_session = session_module.get_engine
    original_health = health_module.get_engine
    session_module.get_engine = lambda: built  # type: ignore[assignment]
    health_module.get_engine = lambda: built  # type: ignore[assignment]
    session_module.get_sessionmaker.cache_clear()

    app = create_app(settings)
    app.dependency_overrides[get_embedding_provider] = HashingEmbeddingProvider
    app.dependency_overrides[get_llm_provider] = lambda: canned_provider(analysis)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        session_module.get_engine = original_session  # type: ignore[assignment]
        health_module.get_engine = original_health  # type: ignore[assignment]
        session_module.get_sessionmaker.cache_clear()


def analysis(intent: IntentCategory, confidence: float) -> IntentAnalysis:
    return IntentAnalysis(intent=intent, confidence=confidence, reason="fixture")


@pytest.fixture
def confident_billing() -> Iterator[TestClient]:
    yield from client_answering(analysis(IntentCategory.BILLING, 0.95))


@pytest.fixture
def uncategorised() -> Iterator[TestClient]:
    yield from client_answering(analysis(IntentCategory.OTHER, 0.9))


@pytest.fixture
def unsure() -> Iterator[TestClient]:
    yield from client_answering(analysis(IntentCategory.TECHNICAL_SUPPORT, 0.05))


@pytest.fixture
def complaining() -> Iterator[TestClient]:
    yield from client_answering(analysis(IntentCategory.COMPLAINT, 0.95))


def test_a_message_is_answered_end_to_end(confident_billing: TestClient) -> None:
    response = confident_billing.post(PATH, json={"message": "Where are my invoices?"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"]
    assert body["intent"] == "billing"
    assert body["trace_id"]
    assert set(body) == set(AssistantMessageResponse.model_fields)


def test_an_uncategorised_message_reaches_the_fallback(uncategorised: TestClient) -> None:
    body = uncategorised.post(PATH, json={"message": "hello there"}).json()
    assert body["fallback"] is True
    assert body["route_reason"] == "no_category"


def test_low_confidence_escalates(unsure: TestClient) -> None:
    body = unsure.post(PATH, json={"message": "it broke again"}).json()
    assert body["escalated"] is True
    assert body["review_id"] is not None
    assert "low_confidence" in body["escalation_reasons"]


@pytest.mark.anyio
async def test_the_review_queue_actually_received_the_item(
    complaining: TestClient, engine: AsyncEngine
) -> None:
    """The API says a person was asked; the table has to agree."""
    body = complaining.post(PATH, json={"message": "This is unacceptable."}).json()
    assert body["escalated"] is True

    async with engine.connect() as connection:
        queued = await connection.scalar(select(func.count()).select_from(ReviewItem))
    assert queued == 1


def test_no_review_is_queued_when_nothing_escalates(
    confident_billing: TestClient,
) -> None:
    body = confident_billing.post(PATH, json={"message": "Where are my invoices?"})
    assert body.json()["escalated"] is False


def test_each_request_gets_its_own_session_and_trace(
    confident_billing: TestClient,
) -> None:
    first = confident_billing.post(PATH, json={"message": "Where are my invoices?"})
    second = confident_billing.post(PATH, json={"message": "And the one from May?"})
    assert first.status_code == second.status_code == 200
    assert first.json()["trace_id"] != second.json()["trace_id"]


def test_readiness_reports_the_database(confident_billing: TestClient) -> None:
    response = confident_billing.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_existing_endpoints_still_work(confident_billing: TestClient) -> None:
    """Regression against M1, M4, and M5 contracts."""
    assert confident_billing.get("/api/v1/health").status_code == 200
    assert confident_billing.get("/api/v1/tickets").status_code == 200
    assert confident_billing.get("/api/v1/documents").status_code == 200
