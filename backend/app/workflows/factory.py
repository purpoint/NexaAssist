"""Assembling a workflow runner for one request."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.embeddings import EmbeddingProvider
from app.services.document import DocumentService
from app.services.ticket import TicketService
from app.tools.execution import ToolExecutor
from app.tools.factory import build_registry
from app.workflows.execution import WorkflowRunner


def build_runner(
    *, session: AsyncSession, embedder: EmbeddingProvider
) -> WorkflowRunner:
    """Build a runner over the same tools the agent uses.

    Deliberately the same registry: a workflow and an agent should not disagree
    about what a tool does.
    """
    registry = build_registry(
        tickets=TicketService(session),
        documents=DocumentService(session, embedder),
    )
    return WorkflowRunner(ToolExecutor(registry))
