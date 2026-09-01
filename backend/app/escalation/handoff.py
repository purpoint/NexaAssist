"""Handing a request to a person.

Sits after routing and policy: by the time this runs, the system already knows
what it would have said and whether policy changed it, which is exactly the
information a reviewer needs.

Queueing failures do not fail the customer's request. A reply the customer can
read is worth more than a queue entry, and losing the entry is recoverable
where dropping the reply is not -- so the failure is logged loudly and the
reply still goes out.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.escalation.criteria import (
    EscalationContext,
    EscalationCriteria,
    EscalationReason,
)
from app.routing.router import RoutedReply
from app.services.review import ReviewQueue

logger = get_logger(__name__)

HANDOFF_NOTICE = " A support agent will follow up with you."


class HandoffResult(BaseModel):
    """What happened to a routed reply once escalation was considered."""

    model_config = ConfigDict(frozen=True)

    reply: str = Field(min_length=1)
    escalated: bool
    reasons: list[EscalationReason] = Field(default_factory=list)
    review_id: uuid.UUID | None = None
    queued: bool = Field(
        default=False,
        description="False when escalation was warranted but could not be recorded.",
    )


class HandoffService:
    """Decides whether a request needs a person, and queues it if so."""

    def __init__(self, queue: ReviewQueue, criteria: EscalationCriteria) -> None:
        self._queue = queue
        self._criteria = criteria

    async def consider(
        self, message: str, routed: RoutedReply, *, ticket_id: uuid.UUID | None = None
    ) -> HandoffResult:
        """Escalate if the criteria call for it, and tell the customer."""
        verdict = self._criteria.evaluate(
            EscalationContext(
                message=message,
                analysis=_analysis_of(routed),
                handled=routed.handled,
                policy_modified=routed.policy_modified,
                policy_rule=routed.policy_rule,
            )
        )

        if not verdict.escalate:
            return HandoffResult(reply=routed.reply, escalated=False)

        review_id, queued = await self._record(message, routed, verdict.primary, ticket_id)
        return HandoffResult(
            reply=_with_notice(routed.reply),
            escalated=True,
            reasons=verdict.reasons,
            review_id=review_id,
            queued=queued,
        )

    async def _record(
        self,
        message: str,
        routed: RoutedReply,
        reason: EscalationReason | None,
        ticket_id: uuid.UUID | None,
    ) -> tuple[uuid.UUID | None, bool]:
        try:
            item = await self._queue.enqueue(
                message=message,
                intent=routed.decision.intent,
                reason=reason or EscalationReason.UNRESOLVED,
                proposed_reply=routed.reply,
                ticket_id=ticket_id,
            )
        except SQLAlchemyError as exc:
            # Type only: a driver message can carry the row it choked on.
            logger.warning(
                "escalation could not be recorded error=%s intent=%s",
                type(exc).__name__,
                routed.decision.intent.value,
            )
            return None, False
        return item.id, True


def _with_notice(reply: str) -> str:
    """Tell the customer a person is involved, without repeating it."""
    if "support agent" in reply.lower():
        return reply
    return reply.rstrip() + HANDOFF_NOTICE


def _analysis_of(routed: RoutedReply):
    from app.schemas.intent import IntentAnalysis

    return IntentAnalysis(
        intent=routed.decision.intent,
        confidence=routed.decision.confidence,
        reason="routed",
    )
