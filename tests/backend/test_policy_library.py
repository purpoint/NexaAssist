"""The shipped policy rules. Offline."""

import pytest

from app.policy.enforcement import PolicyEnforcer
from app.policy.library import (
    REVIEW_REPLY,
    ComplaintsGoToAHuman,
    NoFinancialCommitments,
    UnresolvedRequestsAreNotClaimedAsAnswered,
    default_rules,
)
from app.policy.rules import PolicyAction, PolicyContext, PolicyEngine, PolicyRule
from app.schemas.intent import IntentAnalysis, IntentCategory


def ctx(reply: str, intent: IntentCategory = IntentCategory.BILLING, handled: bool = True):
    return PolicyContext(
        message="m",
        analysis=IntentAnalysis(intent=intent, confidence=0.9, reason="r"),
        proposed_reply=reply,
        handled=handled,
    )


def enforce(reply: str, **kw):
    engine = PolicyEngine(default_rules())
    return PolicyEnforcer(engine).enforce(
        message="m",
        analysis=IntentAnalysis(
            intent=kw.get("intent", IntentCategory.BILLING), confidence=0.9, reason="r"
        ),
        proposed_reply=reply,
        handled=kw.get("handled", True),
    )


def test_all_shipped_rules_satisfy_the_protocol() -> None:
    assert all(isinstance(r, PolicyRule) for r in default_rules())


def test_precedence_order_is_explicit_and_money_first() -> None:
    assert PolicyEngine(default_rules()).names() == [
        "no_financial_commitments",
        "complaints_go_to_a_human",
        "unresolved_requests_reach_a_human",
    ]


# --------------------------------------------------------------------------
# Financial commitments
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        "I have refunded your card.",
        "We will refund you today.",
        "I'll credit your account.",
        "I’ll refund you.",
        "You are guaranteed a full refund.",
        "We have reimbursed the charge.",
    ],
)
def test_financial_commitments_are_replaced(reply: str) -> None:
    """The system has no authority to move money."""
    assert NoFinancialCommitments().evaluate(ctx(reply)).action is PolicyAction.REPLACE
    assert enforce(reply).reply == REVIEW_REPLY


@pytest.mark.parametrize(
    "reply",
    [
        "Refunds usually take five business days.",
        "Our refund policy is documented here.",
        "A support agent can look at the charge.",
    ],
)
def test_describing_refunds_is_not_committing_to_one(reply: str) -> None:
    """The rule must not muzzle ordinary explanation."""
    assert NoFinancialCommitments().evaluate(ctx(reply)) is None
    assert enforce(reply).modified is False


# --------------------------------------------------------------------------
# Complaints
# --------------------------------------------------------------------------


def test_complaints_are_handed_to_a_person() -> None:
    outcome = ComplaintsGoToAHuman().evaluate(ctx("You are mistaken.", IntentCategory.COMPLAINT))

    assert outcome.action is PolicyAction.REPLACE
    assert enforce("You are mistaken.", intent=IntentCategory.COMPLAINT).reply == REVIEW_REPLY


def test_other_intents_are_not_diverted() -> None:
    assert ComplaintsGoToAHuman().evaluate(ctx("Here is how.", IntentCategory.ACCOUNT)) is None


# --------------------------------------------------------------------------
# Unresolved requests
# --------------------------------------------------------------------------


def test_an_unresolved_request_is_not_answered_as_though_resolved() -> None:
    rule = UnresolvedRequestsAreNotClaimedAsAnswered()

    assert rule.evaluate(ctx("Sure, all done!", handled=False)).action is PolicyAction.REPLACE
    assert rule.evaluate(ctx("Sure, all done!", handled=True)) is None


def test_an_unresolved_reply_is_rewritten_end_to_end() -> None:
    enforced = enforce("All sorted.", handled=False)

    assert enforced.reply == REVIEW_REPLY
    assert enforced.rule == "unresolved_requests_reach_a_human"


# --------------------------------------------------------------------------
# Precedence between shipped rules
# --------------------------------------------------------------------------


def test_a_financial_promise_in_a_complaint_is_attributed_to_the_money_rule() -> None:
    enforced = enforce("I have refunded you.", intent=IntentCategory.COMPLAINT)

    assert enforced.rule == "no_financial_commitments"


def test_a_clean_resolved_reply_passes_untouched() -> None:
    enforced = enforce("Your ticket is open and being looked at.")

    assert enforced.modified is False
    assert enforced.action is PolicyAction.ALLOW


def test_the_rules_consult_no_model_or_database() -> None:
    from pathlib import Path

    source = Path("backend/app/policy/library.py").read_text()
    for forbidden in ("app.llm", "groq", "sqlalchemy", "fastapi", "datetime", "random"):
        assert forbidden not in source
