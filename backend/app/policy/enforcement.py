"""Applying policy to what the system is about to say.

The engine decides; this applies the decision. They are separate because the
decision is worth recording whether or not it changed anything, and because
enforcement is where the ordering guarantee actually matters:

**policy is applied after the model has produced its reply, and the model
cannot overrule it.** A reply that policy blocks is never sent, no matter how
confident the model was or what the handler thought it had resolved. Running
policy first and asking the model to respect it would make compliance a
suggestion.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.core.logging import get_logger
from app.policy.rules import (
    PolicyAction,
    PolicyContext,
    PolicyEngine,
    PolicyEvaluation,
)
from app.schemas.intent import IntentAnalysis

logger = get_logger(__name__)

BLOCKED_REPLY = (
    "I am not able to help with that here. A support agent will follow up."
)


class EnforcedReply(BaseModel):
    """The reply after policy, and the record of why it looks like that."""

    model_config = ConfigDict(frozen=True)

    reply: str = Field(min_length=1)
    original_reply: str
    action: PolicyAction
    rule: str
    reason: str
    modified: bool = Field(
        description="True when policy changed what would otherwise have been sent."
    )
    handled: bool = Field(
        description="False when policy blocked, regardless of the handler's view."
    )
    evaluated: list[str] = Field(default_factory=list)


class PolicyEnforcer:
    """Runs the engine over a proposed reply and applies the verdict."""

    def __init__(self, engine: PolicyEngine) -> None:
        self._engine = engine

    def enforce(
        self,
        *,
        message: str,
        analysis: IntentAnalysis,
        proposed_reply: str,
        handled: bool = True,
    ) -> EnforcedReply:
        """Return the reply that may actually be sent."""
        context = PolicyContext(
            message=message,
            analysis=analysis,
            proposed_reply=proposed_reply,
            handled=handled,
        )
        evaluation = self._engine.evaluate(context)
        enforced = self._apply(evaluation, proposed_reply, handled)

        # Rule, action, and reason only. The message and both replies are
        # customer content.
        logger.info(
            "policy applied intent=%s action=%s rule=%s modified=%s handled=%s rules_evaluated=%d",
            analysis.intent.value,
            enforced.action.value,
            enforced.rule,
            enforced.modified,
            enforced.handled,
            len(evaluation.evaluated),
        )
        return enforced

    def _apply(
        self, evaluation: PolicyEvaluation, proposed: str, handled: bool
    ) -> EnforcedReply:
        outcome = evaluation.outcome

        if outcome.action is PolicyAction.BLOCK:
            return EnforcedReply(
                reply=BLOCKED_REPLY,
                original_reply=proposed,
                action=outcome.action,
                rule=outcome.rule,
                reason=outcome.reason,
                modified=True,
                # A blocked reply did not resolve anything, whatever the
                # handler believed.
                handled=False,
                evaluated=evaluation.evaluated,
            )

        if outcome.action is PolicyAction.REPLACE:
            replacement = outcome.replacement or BLOCKED_REPLY
            return EnforcedReply(
                reply=replacement,
                original_reply=proposed,
                action=outcome.action,
                rule=outcome.rule,
                reason=outcome.reason,
                modified=replacement != proposed,
                handled=handled,
                evaluated=evaluation.evaluated,
            )

        return EnforcedReply(
            reply=proposed,
            original_reply=proposed,
            action=PolicyAction.ALLOW,
            rule=outcome.rule,
            reason=outcome.reason,
            modified=False,
            handled=handled,
            evaluated=evaluation.evaluated,
        )
