"""The assistant endpoint.

The first HTTP surface over the answering pipeline. M6 through M16 deliberately
added none: an endpoint is only worth adding once the thing behind it is
finished, and until now the pieces were still being assembled.

Thin, like every other route here: validate, delegate, return. The ordering of
classify, route, and escalate belongs to ``AssistantService``, so it is stated
once rather than re-derived at the edge.

Every dependency is request-scoped. The session comes from the same dependency
every other endpoint uses, so a request that fails rolls back with it, and the
handlers, tools, and agent behind this route hold that session rather than a
global one.
"""

import time
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.limits import enforce_rate_limit
from app.auth.authorization import Authorizer
from app.auth.factory import get_authorizer
from app.auth.identity import RequestIdentity
from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.escalation.factory import build_handoff
from app.llm.base import LLMProvider
from app.llm.factory import get_llm_provider
from app.observability.cost import PricingTable
from app.observability.factory import get_metrics, get_pricing_table, get_tracer
from app.observability.metrics import Metrics
from app.observability.integration import traced_provider
from app.observability.spans import SpanKind
from app.observability.tracer import Tracer
from app.rag.embeddings import EmbeddingProvider
from app.rag.factory import get_embedding_provider
from app.routing.factory import build_router
from app.schemas.assistant import AssistantMessageRequest, AssistantMessageResponse
from app.schemas.common import ErrorResponse
from app.services.assistant import AssistantService
from app.services.conversation import ConversationService
from app.services.intent import IntentService

router = APIRouter(prefix="/assistant", tags=["assistant"])


def get_assistant_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    embedder: Annotated[EmbeddingProvider, Depends(get_embedding_provider)],
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    tracer: Annotated[Tracer, Depends(get_tracer)],
    pricing: Annotated[PricingTable, Depends(get_pricing_table)],
    metrics: Annotated[Metrics, Depends(get_metrics)],
) -> AssistantService:
    """Assemble the pipeline for one request.

    The provider is wrapped for tracing and accounting here rather than in the
    factories, so every model call this request makes -- classification,
    retrieval-grounded answering, and each agent step -- is counted once,
    against one trace, without any of those layers knowing.
    """
    observed = traced_provider(provider, tracer, pricing=pricing, metrics=metrics)
    return AssistantService(
        IntentService(observed),
        build_router(
            session=session, embedder=embedder, provider=observed, settings=settings
        ),
        build_handoff(session=session, settings=settings),
        ConversationService(session),
    )


@router.post(
    "/messages",
    response_model=AssistantMessageResponse,
    summary="Answer a customer message",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
        503: {"model": ErrorResponse, "description": "A dependency is unavailable."},
    },
)
async def answer_message(
    payload: AssistantMessageRequest,
    service: Annotated[AssistantService, Depends(get_assistant_service)],
    tracer: Annotated[Tracer, Depends(get_tracer)],
    identity: Annotated[RequestIdentity, Depends(enforce_rate_limit)],
    authorizer: Annotated[Authorizer, Depends(get_authorizer)],
    metrics: Annotated[Metrics, Depends(get_metrics)],
) -> AssistantMessageResponse:
    """Classify, answer, apply policy, and escalate if a person is needed.

    Errors are not caught here. Every failure the pipeline can produce is
    already an ``AppError`` subclass -- a provider outage, an unreachable
    database -- and M1's handler renders them through ``ErrorResponse``.
    Catching them again would only risk turning a precise status into a 500.
    """
    started = time.perf_counter()
    with tracer.span("assistant.message", SpanKind.REQUEST) as span:
        reply = await service.respond(
            payload.message,
            conversation_id=payload.conversation_id,
            scope=authorizer.scope_for(identity),
        )
        span.set_attributes(
            {
                # The subject is a non-secret label, so it is safe on a span
                # and is what ties a trace to a caller.
                "subject": identity.subject,
                "authenticated": identity.authenticated,
                "intent": reply.intent.value,
                "handler": reply.handler,
                "handled": reply.handled,
                "escalated": reply.escalated,
            }
        )
        # Every label is a bounded category or a flag. The message, the reply
        # and the citations are never labels.
        metrics.increment(
            "assistant_requests_total",
            {
                "intent": reply.intent.value,
                "handler": reply.handler,
                "handled": reply.handled,
                "escalated": reply.escalated,
                "policy_modified": reply.policy_modified,
            },
        )
        metrics.observe(
            "assistant_duration_ms", (time.perf_counter() - started) * 1000.0
        )
        return AssistantMessageResponse(
            **reply.model_dump(exclude={"route_reason"}),
            route_reason=reply.route_reason,
            trace_id=span.trace_id,
        )
