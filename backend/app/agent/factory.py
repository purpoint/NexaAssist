"""Assembling an agent for one request.

The composition root for M7: it wires the M6 registry and executor to the M2
provider and applies the configured budget. Per request, because the tools hold
request-scoped services.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.loop import AgentLoop
from app.agent.state import AgentBudget
from app.core.config import Settings
from app.llm.base import LLMProvider
from app.rag.embeddings import EmbeddingProvider
from app.services.document import DocumentService
from app.services.ticket import TicketService
from app.tools.execution import ToolExecutor
from app.tools.factory import build_registry


def budget_from_settings(settings: Settings) -> AgentBudget:
    return AgentBudget(
        max_steps=settings.agent_max_steps,
        max_tool_calls=settings.agent_max_tool_calls,
    )


def build_agent(
    *,
    session: AsyncSession,
    embedder: EmbeddingProvider,
    provider: LLMProvider,
    settings: Settings,
) -> AgentLoop:
    """Build an agent over the ticket and knowledge-base tools."""
    registry = build_registry(
        tickets=TicketService(session),
        documents=DocumentService(session, embedder),
    )
    return AgentLoop(
        registry,
        ToolExecutor(registry),
        provider,
        budget=budget_from_settings(settings),
    )
