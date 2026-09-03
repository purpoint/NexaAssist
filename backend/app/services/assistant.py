"""The whole answering pipeline, in one place.

Everything it needs already exists: M2 classifies, M8 routes, M10 applies
policy inside routing, M11 decides whether a person is needed. What was missing
was a single object that runs them in the right order, so an endpoint does not
have to know that order -- and so the order is stated once rather than
re-derived by every caller.

Sequence matters and is not negotiable:

1. classify, 2. route (policy runs inside routing, on what would actually be
sent), 3. consider escalation on the reply policy already approved.

Escalation last is the point. Escalating on the handler's draft would ask a
person to look at a reply the customer never received, and skipping it would
send a reply nobody checked.

No FastAPI here, and no provider SDK. This is a service: it takes the pieces it
composes and returns a value.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.escalation.criteria import EscalationReason
from app.escalation.handoff import HandoffService
from app.routing.router import IntentRouter, RouteReason
from app.schemas.intent import IntentCategory
from app.models.conversation import MessageRole
from app.services.conversation import ConversationService
from app.services.intent import IntentService

logger = get_logger(__name__)


class AssistantReply(BaseModel):
    """What the pipeline decided, and what it will say.

    Carries the decision trail as well as the reply. A support system whose
    answers cannot be explained afterwards is one nobody can debug, and every
    field here is a category, a flag, or an identifier -- never content beyond
    the reply itself.
    """

    model_config = ConfigDict(frozen=True)

    reply: str = Field(min_length=1)
    intent: IntentCategory
    confidence: float = Field(ge=0.0, le=1.0)
    handler: str
    route_reason: RouteReason
    fallback: bool
    handled: bool
    policy_rule: str | None = None
    policy_modified: bool = False
    escalated: bool = False
    escalation_reasons: list[EscalationReason] = Field(default_factory=list)
    review_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None


class AssistantService:
    """Runs one customer message through the whole pipeline."""

    def __init__(
        self,
        intent: IntentService,
        router: IntentRouter,
        handoff: HandoffService,
        conversations: ConversationService | None = None,
    ) -> None:
        self._intent = intent
        self._router = router
        self._handoff = handoff
        self._conversations = conversations

    async def respond(
        self, message: str, *, conversation_id: uuid.UUID | None = None
    ) -> AssistantReply:
        """Classify, route, and decide whether a person is needed.

        When a conversation is given, the customer's turn is recorded *before*
        the pipeline runs and the reply after it. Recording the question first
        means a provider outage still leaves evidence that it was asked --
        losing the question because answering failed is the worse outcome.
        """
        if conversation_id is not None:
            await self._record(conversation_id, MessageRole.CUSTOMER, message)

        analysis = await self._intent.analyze(message)
        routed = await self._router.route(message, analysis)
        handoff = await self._handoff.consider(message, routed)

        # Categories, flags, and identifiers. The message and the reply are
        # both customer content and never appear here.
        logger.info(
            "assistant replied intent=%s handler=%s reason=%s handled=%s "
            "policy_modified=%s escalated=%s",
            routed.decision.intent.value,
            routed.decision.handler,
            routed.decision.reason.value,
            routed.handled,
            routed.policy_modified,
            handoff.escalated,
        )

        if conversation_id is not None:
            await self._record(conversation_id, MessageRole.ASSISTANT, handoff.reply)

        return AssistantReply(
            reply=handoff.reply,
            conversation_id=conversation_id,
            intent=routed.decision.intent,
            confidence=routed.decision.confidence,
            handler=routed.decision.handler,
            route_reason=routed.decision.reason,
            fallback=routed.decision.fallback,
            handled=routed.handled,
            policy_rule=routed.policy_rule,
            policy_modified=routed.policy_modified,
            escalated=handoff.escalated,
            escalation_reasons=list(handoff.reasons),
            review_id=handoff.review_id,
        )

    async def _record(
        self, conversation_id: uuid.UUID, role: MessageRole, content: str
    ) -> None:
        """Append one turn.

        Raises through: a conversation id that does not exist is a client
        mistake and deserves its 404, not a silently unrecorded exchange.
        """
        assert self._conversations is not None
        await self._conversations.append(conversation_id, role=role, content=content)
