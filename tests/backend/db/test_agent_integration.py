"""The agent over real tickets and a real knowledge base."""

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.factory import budget_from_settings, build_agent
from app.agent.loop import AgentDecision
from app.core.config import Settings
from app.llm.base import LLMPrompt, LLMUsage, StructuredCompletion
from app.rag.embeddings import HashingEmbeddingProvider
from app.services.ticket import TicketService

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")


class ScriptedProvider:
    name = "scripted"

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self._decisions = decisions
        self.calls = 0

    async def complete_structured(self, *, prompt: LLMPrompt, schema: type, config: Any = None):
        self.calls += 1
        index = min(self.calls - 1, len(self._decisions) - 1)
        return StructuredCompletion[schema](
            output=self._decisions[index],
            provider=self.name,
            model="scripted",
            usage=LLMUsage(),
        )


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        assert await opened.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE
        yield opened
    await engine.dispose()


def agent_over(session: AsyncSession, decisions: list[AgentDecision], **overrides: Any):
    settings = Settings(**overrides)
    return build_agent(
        session=session,
        embedder=HashingEmbeddingProvider(),
        provider=ScriptedProvider(decisions),
        settings=settings,
    )


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_agent_is_wired_to_the_domain_tools(session: AsyncSession) -> None:
    agent = agent_over(session, [AgentDecision(final_answer="hi")])

    assert agent._registry.names() == [
        "list_tickets",
        "lookup_ticket",
        "search_knowledge_base",
    ]


def test_budget_comes_from_settings() -> None:
    budget = budget_from_settings(Settings(agent_max_steps=3, agent_max_tool_calls=2))

    assert budget.max_steps == 3
    assert budget.max_tool_calls == 2


# --------------------------------------------------------------------------
# Real tool use
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_agent_looks_up_a_real_ticket_then_answers(session: AsyncSession) -> None:
    ticket = await TicketService(session).create(
        customer_email="ada@example.com", subject="Charged twice", body="Refund please."
    )
    agent = agent_over(
        session,
        [
            AgentDecision(tool="lookup_ticket", tool_params={"ticket_id": str(ticket.id)}),
            AgentDecision(final_answer="Your ticket is open."),
        ],
    )

    outcome = await agent.run("what is the status of my ticket")

    assert outcome.completed is True
    assert outcome.tool_calls == 1
    assert outcome.steps[0]["outcome"] == "ok"


@pytest.mark.anyio
async def test_agent_searches_the_real_knowledge_base(session: AsyncSession) -> None:
    from app.services.document import DocumentService

    await DocumentService(session, HashingEmbeddingProvider()).ingest(
        title="Refunds", content="Refunds take 5 business days."
    )
    agent = agent_over(
        session,
        [
            AgentDecision(tool="search_knowledge_base", tool_params={"query": "refund"}),
            AgentDecision(final_answer="Five business days."),
        ],
    )

    outcome = await agent.run("how long do refunds take")

    assert outcome.completed is True
    assert outcome.steps[0]["outcome"] == "ok"


@pytest.mark.anyio
async def test_a_missing_ticket_is_recovered_from_not_fatal(session: AsyncSession) -> None:
    import uuid

    agent = agent_over(
        session,
        [
            AgentDecision(tool="lookup_ticket", tool_params={"ticket_id": str(uuid.uuid4())}),
            AgentDecision(final_answer="I could not find that ticket."),
        ],
    )

    outcome = await agent.run("status of my ticket")

    assert outcome.completed is True
    assert outcome.steps[0]["outcome"] == "failed"


@pytest.mark.anyio
async def test_budget_stops_a_runaway_agent_against_real_tools(
    session: AsyncSession,
) -> None:
    agent = agent_over(
        session,
        [AgentDecision(tool="list_tickets", tool_params={})],
        agent_max_steps=2,
        agent_max_tool_calls=9,
    )

    outcome = await agent.run("show me everything")

    assert outcome.completed is False
    assert "step limit" in outcome.stop_reason
    assert outcome.tool_calls == 2


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_outcome_exposes_no_customer_content_or_internals(
    session: AsyncSession,
) -> None:
    ticket = await TicketService(session).create(
        customer_email="private@example.com",
        subject="Card 4242 double charged",
        body="Call me on 555-0100",
    )
    agent = agent_over(
        session,
        [
            AgentDecision(tool="lookup_ticket", tool_params={"ticket_id": str(ticket.id)}),
            AgentDecision(final_answer="Looked into it."),
        ],
    )

    rendered = str((await agent.run("q")).model_dump())

    for leak in ("4242", "555-0100", "private@example.com", "postgresql", "asyncpg"):
        assert leak not in rendered, leak


@pytest.mark.anyio
async def test_logs_record_shape_not_content(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    agent = agent_over(session, [AgentDecision(final_answer="done")])

    with caplog.at_level(logging.INFO, logger="app.agent.loop"):
        await agent.run("my card 4242 was charged")

    assert "steps=1" in caplog.text
    assert "4242" not in caplog.text
