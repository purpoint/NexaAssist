"""Policy rules and deterministic evaluation. Offline."""

import pytest
from pydantic import ValidationError

from app.policy.rules import (
    PolicyAction,
    PolicyContext,
    PolicyEngine,
    PolicyOutcome,
    PolicyRule,
    allow,
)
from app.schemas.intent import IntentAnalysis, IntentCategory


def context(**kw: object) -> PolicyContext:
    base = {
        "message": "why was I charged twice",
        "analysis": IntentAnalysis(
            intent=IntentCategory.BILLING, confidence=0.9, reason="r"
        ),
        "proposed_reply": "I have refunded you.",
    }
    return PolicyContext(**{**base, **kw})


class Rule:
    def __init__(self, name: str, outcome: PolicyOutcome | None) -> None:
        self.name = name
        self.description = f"The {name} rule."
        self._outcome = outcome
        self.seen: list[PolicyContext] = []

    def evaluate(self, ctx: PolicyContext) -> PolicyOutcome | None:
        self.seen.append(ctx)
        return self._outcome


def blocking(name: str = "blocker") -> Rule:
    return Rule(name, PolicyOutcome(action=PolicyAction.BLOCK, rule=name, reason="no"))


def replacing(name: str = "replacer", text: str = "Safe reply.") -> Rule:
    return Rule(
        name,
        PolicyOutcome(
            action=PolicyAction.REPLACE, rule=name, reason="rewritten", replacement=text
        ),
    )


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_a_rule_satisfies_the_protocol_structurally() -> None:
    assert isinstance(Rule("r", None), PolicyRule)


def test_context_is_immutable() -> None:
    """A rule must not be able to edit the facts it is judging."""
    ctx = context()

    with pytest.raises(Exception):
        ctx.proposed_reply = "something else"


def test_an_outcome_needs_an_auditable_reason() -> None:
    with pytest.raises(ValidationError):
        PolicyOutcome(action=PolicyAction.BLOCK, rule="r", reason="")


def test_a_replace_outcome_must_carry_a_replacement() -> None:
    with pytest.raises(ValueError, match="must carry a replacement"):
        PolicyOutcome(action=PolicyAction.REPLACE, rule="r", reason="because")


def test_allow_helper_builds_an_allow() -> None:
    assert allow("r").action is PolicyAction.ALLOW


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_the_same_input_always_yields_the_same_decision() -> None:
    """The whole point of evaluating outside the model."""
    engine = PolicyEngine([blocking()])
    ctx = context()

    first, second = engine.evaluate(ctx), engine.evaluate(ctx)

    assert first.model_dump() == second.model_dump()


def test_rules_see_only_the_context_they_are_given() -> None:
    rule = blocking()
    ctx = context()

    PolicyEngine([rule]).evaluate(ctx)

    assert rule.seen == [ctx]


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------


def test_an_empty_engine_allows() -> None:
    evaluation = PolicyEngine().evaluate(context())

    assert evaluation.allowed
    assert evaluation.outcome.rule == PolicyEngine.NO_RULE


def test_the_first_non_allow_rule_wins() -> None:
    first, second = blocking("first"), replacing("second")

    evaluation = PolicyEngine([first, second]).evaluate(context())

    assert evaluation.outcome.rule == "first"
    assert second.seen == []  # never consulted


def test_abstaining_rules_are_skipped() -> None:
    engine = PolicyEngine([Rule("quiet", None), blocking("loud")])

    evaluation = engine.evaluate(context())

    assert evaluation.outcome.rule == "loud"
    assert evaluation.evaluated == ["quiet", "loud"]


def test_an_explicit_allow_does_not_stop_evaluation() -> None:
    """Allow means 'I have no objection', not 'stop asking'."""
    engine = PolicyEngine([Rule("permissive", allow("permissive")), blocking("strict")])

    assert engine.evaluate(context()).outcome.rule == "strict"


def test_registration_order_is_precedence_order() -> None:
    engine = PolicyEngine([blocking("a"), blocking("b")])

    assert engine.names() == ["a", "b"]
    assert engine.evaluate(context()).outcome.rule == "a"


def test_evaluated_records_only_rules_actually_consulted() -> None:
    engine = PolicyEngine([blocking("a"), blocking("b"), blocking("c")])

    assert engine.evaluate(context()).evaluated == ["a"]


def test_all_rules_are_consulted_when_none_objects() -> None:
    engine = PolicyEngine([Rule("a", None), Rule("b", None)])

    evaluation = engine.evaluate(context())

    assert evaluation.evaluated == ["a", "b"]
    assert evaluation.allowed


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_unnamed_rules_are_rejected() -> None:
    with pytest.raises(ValueError, match="named"):
        PolicyEngine([Rule("", None)])


def test_duplicate_rule_names_are_rejected() -> None:
    """Two rules with one name make a decision unattributable."""
    with pytest.raises(ValueError, match="already registered"):
        PolicyEngine([blocking("dup"), blocking("dup")])


def test_len_reports_the_rule_count() -> None:
    assert len(PolicyEngine([blocking("a"), blocking("b")])) == 2


# --------------------------------------------------------------------------
# Nothing here touches a model
# --------------------------------------------------------------------------


def test_the_policy_module_imports_no_model_or_transport_layer() -> None:
    """Policy must stay reproducible; a provider call would break that."""
    source = (
        __import__("pathlib").Path("backend/app/policy/rules.py").read_text()
    )

    for forbidden in ("app.llm", "groq", "fastapi", "sqlalchemy", "httpx"):
        assert forbidden not in source
