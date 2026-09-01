"""Policy precedence over model output. Offline."""

import logging

import pytest

from app.policy.enforcement import BLOCKED_REPLY, EnforcedReply, PolicyEnforcer
from app.policy.rules import PolicyAction, PolicyContext, PolicyEngine, PolicyOutcome
from app.schemas.intent import IntentAnalysis, IntentCategory

ANALYSIS = IntentAnalysis(intent=IntentCategory.BILLING, confidence=0.95, reason="r")
MODEL_REPLY = "I have refunded your card in full."


class Rule:
    def __init__(self, name: str, outcome: PolicyOutcome | None) -> None:
        self.name = name
        self.description = "d"
        self._outcome = outcome

    def evaluate(self, ctx: PolicyContext) -> PolicyOutcome | None:
        return self._outcome


def enforcer(*rules: Rule) -> PolicyEnforcer:
    return PolicyEnforcer(PolicyEngine(list(rules)))


def block(name: str = "no_refund_promises") -> Rule:
    return Rule(
        name, PolicyOutcome(action=PolicyAction.BLOCK, rule=name, reason="not permitted")
    )


def replace(text: str, name: str = "rewriter") -> Rule:
    return Rule(
        name,
        PolicyOutcome(
            action=PolicyAction.REPLACE, rule=name, reason="rewritten", replacement=text
        ),
    )


def run(e: PolicyEnforcer, reply: str = MODEL_REPLY, handled: bool = True) -> EnforcedReply:
    return e.enforce(
        message="why was I charged twice",
        analysis=ANALYSIS,
        proposed_reply=reply,
        handled=handled,
    )


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------


def test_an_allowed_reply_passes_through_untouched() -> None:
    enforced = run(enforcer(Rule("quiet", None)))

    assert enforced.reply == MODEL_REPLY
    assert enforced.modified is False
    assert enforced.action is PolicyAction.ALLOW


def test_a_blocked_reply_is_never_sent() -> None:
    """The model's output does not survive a block, however confident it was."""
    enforced = run(enforcer(block()))

    assert enforced.reply == BLOCKED_REPLY
    assert MODEL_REPLY not in enforced.reply
    assert enforced.modified is True
    assert enforced.rule == "no_refund_promises"


def test_blocking_overrides_a_handler_that_thought_it_resolved_the_request() -> None:
    """Policy outranks the handler's own view of success."""
    enforced = run(enforcer(block()), handled=True)

    assert enforced.handled is False


def test_a_replacement_substitutes_the_reply() -> None:
    enforced = run(enforcer(replace("A support agent will review this.")))

    assert enforced.reply == "A support agent will review this."
    assert enforced.modified is True
    assert enforced.action is PolicyAction.REPLACE


def test_a_replacement_identical_to_the_original_is_not_a_modification() -> None:
    enforced = run(enforcer(replace(MODEL_REPLY)))

    assert enforced.modified is False


def test_replacement_preserves_the_handler_verdict() -> None:
    """Rewriting the wording does not mean the request went unresolved."""
    assert run(enforcer(replace("Reworded.")), handled=True).handled is True


def test_the_original_reply_is_always_retained_for_audit() -> None:
    enforced = run(enforcer(block()))

    assert enforced.original_reply == MODEL_REPLY


def test_the_first_matching_rule_decides() -> None:
    enforced = run(enforcer(block("first"), replace("later", "second")))

    assert enforced.rule == "first"
    assert enforced.action is PolicyAction.BLOCK


def test_the_evaluated_rules_are_reported() -> None:
    enforced = run(enforcer(Rule("a", None), block("b")))

    assert enforced.evaluated == ["a", "b"]


def test_with_no_rules_the_reply_is_unchanged() -> None:
    enforced = run(enforcer())

    assert enforced.reply == MODEL_REPLY
    assert enforced.rule == PolicyEngine.NO_RULE


# --------------------------------------------------------------------------
# Determinism and shape
# --------------------------------------------------------------------------


def test_enforcement_is_deterministic() -> None:
    e = enforcer(block())

    assert run(e).model_dump() == run(e).model_dump()


def test_the_result_is_immutable() -> None:
    enforced = run(enforcer())

    with pytest.raises(Exception):
        enforced.reply = "tampered"


def test_the_reply_is_never_empty() -> None:
    for e in (enforcer(), enforcer(block()), enforcer(replace("x"))):
        assert run(e).reply


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


def test_logs_record_the_decision_not_the_replies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.policy.enforcement"):
        enforcer(block()).enforce(
            message="my card 4242 was charged",
            analysis=ANALYSIS,
            proposed_reply="I refunded card 4242.",
        )

    assert "action=block" in caplog.text and "rule=no_refund_promises" in caplog.text
    assert "4242" not in caplog.text
