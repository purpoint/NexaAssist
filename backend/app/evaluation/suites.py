"""The shipped evaluation suites.

What can honestly be evaluated offline, and what cannot, is the whole design
of this module.

The deterministic layers *can* be. Policy decisions and escalation criteria are
pure functions of their input — that is exactly what M10 and M11 committed to —
so a case with a different input has a genuinely different expected output, and
a suite over them means something.

Model output *cannot* be, not here. ``StaticLLMProvider`` returns one canned
response per schema regardless of input, so an offline suite over intent
classification would be every case expecting the same answer: a suite that
passes whatever the prompt says. Rather than build that and call it coverage,
the model-facing regression guard is a different thing entirely — the prompt
digests pinned in the regression tests, which fail when prompt text changes
without a version bump.

Evaluating the model itself needs a real provider, which is an operator action
against a live key, not something a test suite does.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.escalation.criteria import EscalationContext, EscalationCriteria
from app.evaluation.cases import EvalSuite, build_suite
from app.evaluation.checks import Check, FieldEquals, FieldIn, TextContainsNone
from app.evaluation.results import EvalReport
from app.evaluation.runner import EvalTarget, EvaluationRunner
from app.policy.enforcement import PolicyEnforcer
from app.policy.library import default_rules
from app.policy.rules import PolicyEngine
from app.schemas.intent import IntentAnalysis, IntentCategory


def _analysis(inputs: Mapping[str, Any]) -> IntentAnalysis:
    """Build the classification a case describes.

    Supplied by the case rather than produced by a model: these suites are
    about what the deterministic layers do *given* a classification.
    """
    return IntentAnalysis(
        intent=IntentCategory(inputs.get("intent", "other")),
        confidence=float(inputs.get("confidence", 0.9)),
        reason=inputs.get("reason", "case fixture"),
    )


class PolicyTarget:
    """Runs the shipped policy rules over a proposed reply."""

    name = "policy"

    def __init__(self) -> None:
        self._enforcer = PolicyEnforcer(PolicyEngine(list(default_rules())))

    async def run(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        enforced = self._enforcer.enforce(
            message=inputs["message"],
            analysis=_analysis(inputs),
            proposed_reply=inputs["proposed_reply"],
            handled=bool(inputs.get("handled", True)),
        )
        return {
            "reply": enforced.reply,
            "modified": enforced.modified,
            "rule": enforced.rule,
            "handled": enforced.handled,
        }


class EscalationTarget:
    """Runs the shipped escalation criteria."""

    name = "escalation"

    def __init__(self) -> None:
        self._criteria = EscalationCriteria()

    async def run(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        verdict = self._criteria.evaluate(
            EscalationContext(
                message=inputs["message"],
                analysis=_analysis(inputs),
                handled=bool(inputs.get("handled", True)),
                policy_modified=bool(inputs.get("policy_modified", False)),
                policy_rule=inputs.get("policy_rule"),
            )
        )
        return {
            "escalate": verdict.escalate,
            "primary": verdict.primary.value if verdict.primary else None,
        }


POLICY_SUITE: EvalSuite = build_suite(
    "policy",
    [
        {
            "id": "plain_answer_is_allowed",
            "inputs": {
                "message": "How do I change my address?",
                "intent": "account",
                "proposed_reply": "You can change it under Settings.",
            },
            "expectations": {"modified": False},
            "tags": ("allow",),
        },
        {
            "id": "refund_promise_is_stopped",
            "inputs": {
                "message": "I want my money back.",
                "intent": "billing",
                "proposed_reply": "I have issued a full refund to your card.",
            },
            "expectations": {"modified": True, "forbidden": ["issued a full refund"]},
            "tags": ("financial",),
        },
        {
            "id": "complaint_reaches_a_human",
            "inputs": {
                "message": "This is the third time this has broken.",
                "intent": "complaint",
                "proposed_reply": "Sorry about that.",
            },
            "expectations": {"modified": True},
            "tags": ("complaint",),
        },
        {
            "id": "unresolved_is_not_claimed_answered",
            "inputs": {
                "message": "Why was I charged twice?",
                "intent": "billing",
                "proposed_reply": "All sorted.",
                "handled": False,
            },
            "expectations": {"modified": True},
            "tags": ("unresolved",),
        },
    ],
)

ESCALATION_SUITE: EvalSuite = build_suite(
    "escalation",
    [
        {
            "id": "confident_and_handled_stays_automated",
            "inputs": {
                "message": "Where do I find my invoices?",
                "intent": "billing",
                "confidence": 0.95,
            },
            "expectations": {"escalate": False},
            "tags": ("automated",),
        },
        {
            "id": "low_confidence_goes_to_a_human",
            "inputs": {
                "message": "it does the thing again",
                "intent": "technical_support",
                "confidence": 0.1,
            },
            "expectations": {"escalate": True, "primary": "low_confidence"},
            "tags": ("confidence",),
        },
        {
            "id": "unhandled_goes_to_a_human",
            "inputs": {
                "message": "Why was I charged twice?",
                "intent": "billing",
                "confidence": 0.95,
                "handled": False,
            },
            "expectations": {"escalate": True, "primary": "unresolved"},
            "tags": ("unresolved",),
        },
        {
            "id": "complaint_goes_to_a_human",
            "inputs": {
                "message": "This is unacceptable.",
                "intent": "complaint",
                "confidence": 0.95,
            },
            "expectations": {"escalate": True},
            "tags": ("complaint",),
        },
    ],
)

POLICY_CHECKS: tuple[Check, ...] = (
    FieldEquals("modified"),
    TextContainsNone("reply"),
)

ESCALATION_CHECKS: tuple[Check, ...] = (
    FieldEquals("escalate"),
    FieldIn("escalate", [True, False]),
)


@dataclass(frozen=True)
class EvaluationPlan:
    """A suite, the target it runs against, and how it is judged."""

    suite: EvalSuite
    target: EvalTarget
    checks: tuple[Check, ...]


def default_plans() -> tuple[EvaluationPlan, ...]:
    """Every suite that ships, in a stable order."""
    return (
        EvaluationPlan(POLICY_SUITE, PolicyTarget(), POLICY_CHECKS),
        EvaluationPlan(ESCALATION_SUITE, EscalationTarget(), ESCALATION_CHECKS),
    )


async def run_plan(plan: EvaluationPlan) -> EvalReport:
    """Run one plan and return its report."""
    return await EvaluationRunner(plan.target, plan.checks).run(plan.suite)


async def run_all(plans: Sequence[EvaluationPlan] | None = None) -> list[EvalReport]:
    """Run every plan, in order."""
    return [await run_plan(plan) for plan in (plans or default_plans())]
