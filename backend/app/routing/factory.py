"""Wiring intents to handlers.

The whole routing table in one readable block. Which capability serves which
intent is a product decision, so it should be visible rather than inferred.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.factory import build_agent
from app.core.config import Settings
from app.llm.base import LLMProvider
from app.policy.enforcement import PolicyEnforcer
from app.policy.library import default_rules
from app.policy.rules import PolicyEngine
from app.rag.embeddings import EmbeddingProvider
from app.routing.handlers import IntentHandler
from app.routing.intent_handlers import (
    AgentHandler,
    DocumentedFirstHandler,
    FallbackHandler,
    KnowledgeBaseHandler,
)
from app.routing.registry import HandlerRegistry
from app.routing.router import IntentRouter
from app.schemas.intent import IntentCategory
from app.services.answer import AnswerService
from app.services.document import DocumentService


def build_router(
    *,
    session: AsyncSession,
    embedder: EmbeddingProvider,
    provider: LLMProvider,
    settings: Settings,
) -> IntentRouter:
    """Assemble the routing table for one request."""
    documents = DocumentService(session, embedder)
    knowledge_base: IntentHandler = KnowledgeBaseHandler(
        AnswerService(documents, provider), top_k=settings.retrieval_top_k
    )
    agent: IntentHandler = AgentHandler(
        build_agent(
            session=session, embedder=embedder, provider=provider, settings=settings
        )
    )

    registry = HandlerRegistry()
    # Documented answers.
    registry.register(IntentCategory.PRODUCT_QUESTION, knowledge_base)
    registry.register(IntentCategory.ACCOUNT, knowledge_base)
    # Billing splits in two and the classifier cannot split it: "what is your
    # refund window" is a documented policy, "I was charged twice" needs
    # somebody to look at an account, and both arrive as BILLING. So the
    # documentation is asked first and the agent gets what it cannot answer.
    # Routing straight to the agent made the refund policy unreachable through
    # the assistant -- it was retrieved by /documents/answer and escalated by
    # the assistant, which is the same question getting two different answers.
    registry.register(
        IntentCategory.BILLING, DocumentedFirstHandler(knowledge_base, agent)
    )
    # Needs to inspect account state, so the agent and its tools.
    registry.register(IntentCategory.TECHNICAL_SUPPORT, agent)
    # Not documented-first on purpose: somebody complaining wants a person,
    # and answering from a policy document reads as being brushed off.
    registry.register(IntentCategory.COMPLAINT, agent)
    # OTHER always reaches the fallback via the router's no-category rule; it is
    # registered anyway so the table is complete and require_complete() means
    # what it says.
    fallback = FallbackHandler()
    registry.register(IntentCategory.OTHER, fallback)

    registry.require_complete()
    return IntentRouter(
        registry,
        fallback,
        min_confidence=settings.routing_min_confidence,
        enforcer=PolicyEnforcer(PolicyEngine(default_rules())),
    )
