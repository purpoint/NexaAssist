"""The routing table over real services."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.loop import AgentDecision
from app.core.config import Settings
from app.llm.base import LLMPrompt, LLMUsage, StructuredCompletion
from app.rag.embeddings import HashingEmbeddingProvider
from app.routing.factory import build_router
from app.policy.library import REVIEW_REPLY
from app.routing.router import RouteReason
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.answer import GroundedModelAnswer
from app.services.document import DocumentService
from app.services.ticket import TicketService

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")

REFUNDS = "Refunds take 5 business days.\n\nContact billing to start one."


class SchemaAwareProvider:
    """Answers whichever structured schema it is asked for."""

    name = "scripted"

    def __init__(self, *, answer: GroundedModelAnswer, decision: AgentDecision) -> None:
        self._answer = answer
        self._decision = decision
        self.schemas: list[str] = []

    async def complete_structured(self, *, prompt: LLMPrompt, schema: type, config: Any = None):
        self.schemas.append(schema.__name__)
        output = self._answer if schema.__name__ == "GroundedModelAnswer" else self._decision
        return StructuredCompletion[schema](
            output=output, provider=self.name, model="scripted", usage=LLMUsage()
        )


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        assert await opened.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE
        yield opened
    await engine.dispose()


def router_over(session: AsyncSession, provider: Any, **overrides: Any):
    return build_router(
        session=session,
        embedder=HashingEmbeddingProvider(),
        provider=provider,
        settings=Settings(**overrides),
    )


def provider(
    *, answered: bool = True, final_answer: str = "Your ticket is open."
) -> SchemaAwareProvider:
    return SchemaAwareProvider(
        answer=GroundedModelAnswer(
            answered=answered, answer="Five business days.", cited_sources=[1]
        ),
        decision=AgentDecision(final_answer=final_answer),
    )


def analysis(intent: IntentCategory, confidence: float = 0.9) -> IntentAnalysis:
    return IntentAnalysis(intent=intent, confidence=confidence, reason="r")


# --------------------------------------------------------------------------
# Table completeness
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_every_intent_category_has_a_handler(session: AsyncSession) -> None:
    """require_complete() runs at wiring time; this pins that it passes."""
    router = router_over(session, provider())

    for category in IntentCategory:
        assert router.decide(analysis(category)).handler


# --------------------------------------------------------------------------
# Documented answers go to the knowledge base
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_product_question_is_answered_from_the_knowledge_base(
    session: AsyncSession,
) -> None:
    await DocumentService(session, HashingEmbeddingProvider()).ingest(
        title="Refunds", content=REFUNDS
    )
    used = provider()

    reply = await router_over(session, used).route(
        "how long do refunds take", analysis(IntentCategory.PRODUCT_QUESTION)
    )

    assert reply.decision.handler == "knowledge_base"
    assert reply.decision.reason is RouteReason.MATCHED
    assert reply.reply == "Five business days."
    assert "GroundedModelAnswer" in used.schemas


@pytest.mark.anyio
async def test_the_knowledge_base_reports_honestly_when_undocumented(
    session: AsyncSession,
) -> None:
    """No documents ingested: the grounded answerer must not invent one."""
    reply = await router_over(session, provider()).route(
        "how long do refunds take", analysis(IntentCategory.PRODUCT_QUESTION)
    )

    assert reply.handled is False


# --------------------------------------------------------------------------
# Account-state questions go to the agent
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_billing_question_runs_the_agent(session: AsyncSession) -> None:
    await TicketService(session).create(
        customer_email="ada@example.com", subject="Charged twice", body="Refund please."
    )
    used = provider(final_answer="Your ticket is open.")

    reply = await router_over(session, used).route(
        "why was I charged twice", analysis(IntentCategory.BILLING)
    )

    assert reply.decision.handler == "agent"
    assert reply.reply == "Your ticket is open."
    assert "AgentDecision" in used.schemas


@pytest.mark.parametrize(
    "category",
    [IntentCategory.BILLING, IntentCategory.TECHNICAL_SUPPORT, IntentCategory.COMPLAINT],
)
@pytest.mark.anyio
async def test_account_state_intents_route_to_the_agent(
    session: AsyncSession, category: IntentCategory
) -> None:
    assert router_over(session, provider()).decide(analysis(category)).handler == "agent"


@pytest.mark.parametrize(
    "category", [IntentCategory.PRODUCT_QUESTION, IntentCategory.ACCOUNT]
)
@pytest.mark.anyio
async def test_documented_intents_route_to_the_knowledge_base(
    session: AsyncSession, category: IntentCategory
) -> None:
    assert (
        router_over(session, provider()).decide(analysis(category)).handler
        == "knowledge_base"
    )


# --------------------------------------------------------------------------
# Fallback
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_other_reaches_the_fallback_without_guessing(
    session: AsyncSession,
) -> None:
    reply = await router_over(session, provider()).route(
        "tell me a joke", analysis(IntentCategory.OTHER, 0.99)
    )

    assert reply.decision.reason is RouteReason.NO_CATEGORY
    assert reply.handled is False
    # M10: the fallback's own wording is now replaced by policy, because an
    # unresolved request must not be answered as though it were resolved.
    assert reply.reply == REVIEW_REPLY
    assert reply.policy_rule == "unresolved_requests_reach_a_human"
    assert reply.policy_modified is True


@pytest.mark.anyio
async def test_a_low_confidence_classification_is_not_acted_on(
    session: AsyncSession,
) -> None:
    used = provider()

    reply = await router_over(session, used, routing_min_confidence=0.8).route(
        "maybe billing", analysis(IntentCategory.BILLING, 0.3)
    )

    assert reply.decision.reason is RouteReason.LOW_CONFIDENCE
    assert reply.reply == REVIEW_REPLY  # policy rewrote the fallback wording
    assert reply.handled is False
    assert used.schemas == []  # no model was consulted at all


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replies_expose_no_internals(session: AsyncSession) -> None:
    await TicketService(session).create(
        customer_email="private@example.com",
        subject="Card 4242 charged",
        body="Call 555-0100",
    )

    reply = await router_over(session, provider()).route(
        "why was I charged", analysis(IntentCategory.BILLING)
    )

    rendered = str(reply.model_dump())
    for leak in ("postgresql", "asyncpg", "Traceback", "555-0100"):
        assert leak not in rendered


@pytest.mark.anyio
async def test_logs_carry_routing_not_content(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.routing.router"):
        await router_over(session, provider()).route(
            "my card 4242 was charged", analysis(IntentCategory.BILLING)
        )

    assert "intent=billing" in caplog.text
    assert "4242" not in caplog.text
