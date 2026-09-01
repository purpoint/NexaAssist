"""Choosing which handler serves a message.

Three things can go wrong with a classification, and all three route to the
same place -- the fallback -- for different reasons that must stay
distinguishable in the logs:

* the model chose ``other``, meaning none of the categories fit;
* the model chose a category but was not confident enough to act on it;
* the category has no registered handler.

Confidence is the model's own self-report, not a calibrated probability (see
``docs/prompt.md``). The threshold is therefore a coarse guard against acting
on a guess, not a statistical decision.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.policy.enforcement import PolicyEnforcer
from app.routing.handlers import HandlerRequest, HandlerResponse, IntentHandler
from app.routing.registry import HandlerRegistry
from app.schemas.intent import IntentAnalysis, IntentCategory

logger = get_logger(__name__)

DEFAULT_MIN_CONFIDENCE = 0.5


class RouteReason(StrEnum):
    """Why a message went where it did."""

    MATCHED = "matched"
    NO_CATEGORY = "no_category"
    LOW_CONFIDENCE = "low_confidence"
    NO_HANDLER = "no_handler"


class RoutingDecision(BaseModel):
    """Which handler was chosen, and why."""

    model_config = ConfigDict(frozen=True)

    intent: IntentCategory
    confidence: float = Field(ge=0.0, le=1.0)
    handler: str
    reason: RouteReason
    fallback: bool


class RoutedReply(BaseModel):
    """A handled message: the decision plus what the handler said."""

    model_config = ConfigDict(frozen=True)

    decision: RoutingDecision
    reply: str
    handled: bool
    policy_rule: str | None = Field(
        default=None, description="The policy rule that decided the final reply."
    )
    policy_modified: bool = Field(
        default=False, description="True when policy changed what would have been sent."
    )


class IntentRouter:
    """Routes a classified message to a handler, or to the fallback."""

    def __init__(
        self,
        registry: HandlerRegistry,
        fallback: IntentHandler,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        enforcer: PolicyEnforcer | None = None,
    ) -> None:
        self._registry = registry
        self._fallback = fallback
        self._min_confidence = min_confidence
        self._enforcer = enforcer

    def decide(self, analysis: IntentAnalysis) -> RoutingDecision:
        """Choose a handler without running it.

        Separate from ``route`` so the choice can be inspected and tested
        without side effects.
        """
        if analysis.intent is IntentCategory.OTHER:
            return self._to_fallback(analysis, RouteReason.NO_CATEGORY)

        if analysis.confidence < self._min_confidence:
            return self._to_fallback(analysis, RouteReason.LOW_CONFIDENCE)

        handler = self._registry.get(analysis.intent)
        if handler is None:
            return self._to_fallback(analysis, RouteReason.NO_HANDLER)

        return RoutingDecision(
            intent=analysis.intent,
            confidence=analysis.confidence,
            handler=handler.name,
            reason=RouteReason.MATCHED,
            fallback=False,
        )

    async def route(self, message: str, analysis: IntentAnalysis) -> RoutedReply:
        """Choose a handler and run it."""
        decision = self.decide(analysis)
        handler = (
            self._fallback
            if decision.fallback
            else self._registry.get(analysis.intent) or self._fallback
        )

        response: HandlerResponse = await handler.handle(
            HandlerRequest(message=message, analysis=analysis)
        )

        # Policy runs after the handler, on what would actually be sent, and
        # the handler cannot overrule it.
        reply, handled = response.reply, response.handled
        policy_rule: str | None = None
        policy_modified = False
        if self._enforcer is not None:
            enforced = self._enforcer.enforce(
                message=message,
                analysis=analysis,
                proposed_reply=response.reply,
                handled=response.handled,
            )
            reply, handled = enforced.reply, enforced.handled
            policy_rule, policy_modified = enforced.rule, enforced.modified

        # Intent, confidence, and routing reason only. The message and the
        # reply are customer content.
        logger.info(
            "routed intent=%s confidence=%.2f handler=%s reason=%s fallback=%s handled=%s",
            decision.intent.value,
            decision.confidence,
            response.handler,
            decision.reason.value,
            decision.fallback,
            handled,
        )
        return RoutedReply(
            decision=decision,
            reply=reply,
            handled=handled,
            policy_rule=policy_rule,
            policy_modified=policy_modified,
        )

    def _to_fallback(
        self, analysis: IntentAnalysis, reason: RouteReason
    ) -> RoutingDecision:
        return RoutingDecision(
            intent=analysis.intent,
            confidence=analysis.confidence,
            handler=self._fallback.name,
            reason=reason,
            fallback=True,
        )
