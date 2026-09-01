"""Escalation criteria. Offline and deterministic."""

import pytest

from app.escalation.criteria import (
    EscalationContext,
    EscalationCriteria,
    EscalationReason,
)
from app.schemas.intent import IntentAnalysis, IntentCategory


def ctx(**kw: object) -> EscalationContext:
    base = {
        "message": "why was I charged twice",
        "analysis": IntentAnalysis(
            intent=kw.pop("intent", IntentCategory.BILLING),
            confidence=kw.pop("confidence", 0.9),
            reason="r",
        ),
    }
    return EscalationContext(**{**base, **kw})


CRITERIA = EscalationCriteria()


def test_a_resolved_confident_request_stays_automated() -> None:
    verdict = CRITERIA.evaluate(ctx())

    assert verdict.escalate is False
    assert verdict.reasons == []
    assert verdict.primary is None


def test_an_unresolved_request_escalates() -> None:
    verdict = CRITERIA.evaluate(ctx(handled=False))

    assert verdict.escalate is True
    assert EscalationReason.UNRESOLVED in verdict.reasons


def test_a_complaint_escalates_even_when_handled() -> None:
    verdict = CRITERIA.evaluate(ctx(intent=IntentCategory.COMPLAINT))

    assert verdict.reasons == [EscalationReason.COMPLAINT]


def test_an_uncategorised_request_escalates() -> None:
    verdict = CRITERIA.evaluate(ctx(intent=IntentCategory.OTHER))

    assert EscalationReason.UNCATEGORISED in verdict.reasons


def test_low_confidence_escalates() -> None:
    verdict = EscalationCriteria(min_confidence=0.8).evaluate(ctx(confidence=0.4))

    assert EscalationReason.LOW_CONFIDENCE in verdict.reasons


def test_other_is_not_also_reported_as_low_confidence() -> None:
    """One situation, one reason -- double counting distorts queue triage."""
    verdict = CRITERIA.evaluate(ctx(intent=IntentCategory.OTHER, confidence=0.1))

    assert verdict.reasons == [EscalationReason.UNCATEGORISED]


def test_a_policy_modification_escalates() -> None:
    verdict = CRITERIA.evaluate(ctx(policy_modified=True, policy_rule="no_financial_commitments"))

    assert EscalationReason.POLICY in verdict.reasons


def test_reasons_accumulate_rather_than_short_circuit() -> None:
    """Unresolved AND a complaint reads very differently from either alone."""
    verdict = CRITERIA.evaluate(
        ctx(intent=IntentCategory.COMPLAINT, handled=False, policy_modified=True)
    )

    assert verdict.reasons == [
        EscalationReason.UNRESOLVED,
        EscalationReason.COMPLAINT,
        EscalationReason.POLICY,
    ]
    assert verdict.primary is EscalationReason.UNRESOLVED


@pytest.mark.parametrize(
    ("confidence", "escalates"), [(0.49, True), (0.5, False)], ids=["below", "at"]
)
def test_the_confidence_threshold_is_inclusive(confidence: float, escalates: bool) -> None:
    assert CRITERIA.evaluate(ctx(confidence=confidence)).escalate is escalates


def test_evaluation_is_deterministic() -> None:
    context = ctx(handled=False)

    assert CRITERIA.evaluate(context) == CRITERIA.evaluate(context)


def test_context_and_verdict_are_immutable() -> None:
    with pytest.raises(Exception):
        ctx().handled = False


def test_the_criteria_consult_no_model_or_database() -> None:
    from pathlib import Path

    source = Path("backend/app/escalation/criteria.py").read_text()
    for forbidden in ("app.llm", "groq", "sqlalchemy", "fastapi", "random"):
        assert forbidden not in source
