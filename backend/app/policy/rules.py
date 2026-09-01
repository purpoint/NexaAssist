"""Policy rules, evaluated outside the model.

A rule is a plain function over facts the system already has -- the message,
its classification, and the reply about to be sent. No rule may call a model,
open a connection, or depend on anything that could vary between runs: the
whole value of a policy engine is that the same input always yields the same
decision, and that the decision can be explained without replaying a
generation.

Rules are ordered, and the first non-``ALLOW`` outcome wins. Ordering is the
precedence mechanism, and it is explicit rather than emergent.
"""

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.intent import IntentAnalysis


class PolicyAction(StrEnum):
    """What a rule wants to happen."""

    ALLOW = "allow"
    BLOCK = "block"
    REPLACE = "replace"


class PolicyContext(BaseModel):
    """The facts a rule may consider.

    Everything a rule can see is here, so a rule cannot quietly depend on
    ambient state and become unreproducible.
    """

    model_config = ConfigDict(frozen=True)

    message: str
    analysis: IntentAnalysis
    proposed_reply: str = Field(
        description="What the system intends to say, before policy is applied."
    )
    handled: bool = Field(
        default=True, description="Whether the handler considered it resolved."
    )


class PolicyOutcome(BaseModel):
    """A rule's verdict."""

    model_config = ConfigDict(frozen=True)

    action: PolicyAction
    rule: str
    reason: str = Field(min_length=1, description="Why, in terms a human can audit.")
    replacement: str | None = Field(
        default=None, description="Required when the action is REPLACE."
    )

    def model_post_init(self, _: object) -> None:
        if self.action is PolicyAction.REPLACE and not self.replacement:
            raise ValueError("A REPLACE outcome must carry a replacement reply.")


@runtime_checkable
class PolicyRule(Protocol):
    """A deterministic check over a :class:`PolicyContext`."""

    name: str
    description: str

    def evaluate(self, context: PolicyContext) -> PolicyOutcome | None:
        """Return an outcome, or ``None`` to abstain."""
        ...


def allow(rule: str, reason: str = "no rule applied") -> PolicyOutcome:
    return PolicyOutcome(action=PolicyAction.ALLOW, rule=rule, reason=reason)


class PolicyEvaluation(BaseModel):
    """The engine's decision, plus which rules were consulted."""

    model_config = ConfigDict(frozen=True)

    outcome: PolicyOutcome
    evaluated: list[str] = Field(
        default_factory=list, description="Rules consulted, in order, up to the decision."
    )

    @property
    def allowed(self) -> bool:
        return self.outcome.action is PolicyAction.ALLOW


class PolicyEngine:
    """Evaluates an ordered set of rules and reports the first decision."""

    NO_RULE = "default_allow"

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self._rules: list[PolicyRule] = []
        for rule in rules or []:
            self.add(rule)

    def add(self, rule: PolicyRule) -> None:
        if not getattr(rule, "name", ""):
            raise ValueError("Policy rules must be named so a decision can be attributed.")
        if any(existing.name == rule.name for existing in self._rules):
            raise ValueError(f"A rule named {rule.name!r} is already registered.")
        self._rules.append(rule)

    def names(self) -> list[str]:
        """Rule names in evaluation order -- which is precedence order."""
        return [rule.name for rule in self._rules]

    def evaluate(self, context: PolicyContext) -> PolicyEvaluation:
        """Return the first non-allow outcome, or a default allow."""
        consulted: list[str] = []
        for rule in self._rules:
            consulted.append(rule.name)
            outcome = rule.evaluate(context)
            if outcome is None or outcome.action is PolicyAction.ALLOW:
                continue
            return PolicyEvaluation(outcome=outcome, evaluated=consulted)

        return PolicyEvaluation(
            outcome=allow(self.NO_RULE), evaluated=consulted
        )

    def __len__(self) -> int:
        return len(self._rules)
