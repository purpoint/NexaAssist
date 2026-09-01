"""When a request needs a person.

Evaluated outside the model, from facts the system already has: what the
customer wrote, how it was classified, what the handler managed, and what
policy did to the reply.

Each reason is separate rather than collapsed into a boolean, because "why did
this reach a human" is the question anyone reviewing the queue will ask first,
and because the mix of reasons is how you tell an underperforming classifier
from an overcautious policy.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intent import IntentAnalysis, IntentCategory

DEFAULT_MIN_CONFIDENCE = 0.5


class EscalationReason(StrEnum):
    """Why a request was escalated."""

    UNRESOLVED = "unresolved"
    LOW_CONFIDENCE = "low_confidence"
    UNCATEGORISED = "uncategorised"
    POLICY = "policy"
    COMPLAINT = "complaint"


class EscalationContext(BaseModel):
    """The facts the criteria consider."""

    model_config = ConfigDict(frozen=True)

    message: str = Field(min_length=1)
    analysis: IntentAnalysis
    handled: bool = True
    policy_modified: bool = False
    policy_rule: str | None = None


class EscalationVerdict(BaseModel):
    """Whether a person is needed, and why."""

    model_config = ConfigDict(frozen=True)

    escalate: bool
    reasons: list[EscalationReason] = Field(default_factory=list)

    @property
    def primary(self) -> EscalationReason | None:
        """The first reason, used when one label is needed."""
        return self.reasons[0] if self.reasons else None


class EscalationCriteria:
    """Decides whether a request goes to a human.

    Reasons accumulate rather than short-circuit: a request that is both
    unresolved *and* a complaint should say so, because that combination reads
    very differently in a queue from either alone.
    """

    def __init__(self, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> None:
        self._min_confidence = min_confidence

    def evaluate(self, context: EscalationContext) -> EscalationVerdict:
        reasons: list[EscalationReason] = []

        if not context.handled:
            reasons.append(EscalationReason.UNRESOLVED)

        if context.analysis.intent is IntentCategory.COMPLAINT:
            reasons.append(EscalationReason.COMPLAINT)

        if context.analysis.intent is IntentCategory.OTHER:
            reasons.append(EscalationReason.UNCATEGORISED)
        elif context.analysis.confidence < self._min_confidence:
            # Only meaningful for a real category: "other" is already covered,
            # and reporting both would double-count one situation.
            reasons.append(EscalationReason.LOW_CONFIDENCE)

        if context.policy_modified:
            reasons.append(EscalationReason.POLICY)

        return EscalationVerdict(escalate=bool(reasons), reasons=reasons)
