"""What "correct" means for one case.

Checks are pure functions of the case and the output — no provider call, no
database, no clock — so the same pair always yields the same verdict. The same
discipline as the M10 policy rules, and for the same reason: a judgement that
varies run to run cannot be used to decide whether a change made things worse.

Deliberately shallow. Every check here answers a question about *structure* or
*containment*, not about meaning. Judging whether prose is a good answer is not
something a string comparison does, and pretending otherwise produces a suite
that is confidently wrong.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from app.evaluation.cases import EvalCase
from app.evaluation.errors import EvaluationDefinitionError
from app.evaluation.results import CheckOutcome, CheckResult


@runtime_checkable
class Check(Protocol):
    """A verdict on one case's output."""

    name: str

    def evaluate(self, case: EvalCase, output: Any) -> CheckResult:
        """Judge ``output``. Must not raise: a broken check is a failed check."""
        ...


def _read(output: Any, field: str) -> Any:
    """Read a field from an object or a mapping, whichever it is."""
    if isinstance(output, dict):
        return output.get(field)
    return getattr(output, field, None)


def _passed(name: str) -> CheckResult:
    return CheckResult(check=name, outcome=CheckOutcome.PASSED)


def _failed(name: str, detail: str) -> CheckResult:
    return CheckResult(check=name, outcome=CheckOutcome.FAILED, detail=detail)


class FieldEquals:
    """The named output field equals the case's expectation."""

    def __init__(self, field: str, *, expectation: str | None = None) -> None:
        if not field:
            raise EvaluationDefinitionError("A field name is required.")
        self.field = field
        self.expectation = expectation or field
        self.name = f"field_equals:{field}"

    def evaluate(self, case: EvalCase, output: Any) -> CheckResult:
        if self.expectation not in case.expectations:
            return _failed(self.name, f"case declares no {self.expectation!r}")
        expected = case.expectations[self.expectation]
        actual = _read(output, self.field)
        if actual == expected:
            return _passed(self.name)
        # Both values are the suite's own vocabulary -- category names and
        # flags -- not model prose, so quoting them is safe and is the whole
        # point of a failure message.
        return _failed(self.name, f"expected {expected!r}, got {actual!r}")


class FieldIn:
    """The named output field is one of an allowed set.

    For fields where several answers are acceptable and the suite should not
    pretend otherwise.
    """

    def __init__(self, field: str, allowed: Sequence[Any]) -> None:
        if not allowed:
            raise EvaluationDefinitionError("An allowed set may not be empty.")
        self.field = field
        self.allowed = tuple(allowed)
        self.name = f"field_in:{field}"

    def evaluate(self, case: EvalCase, output: Any) -> CheckResult:
        actual = _read(output, self.field)
        if actual in self.allowed:
            return _passed(self.name)
        return _failed(self.name, f"{actual!r} not in {list(self.allowed)!r}")


class TextContainsAll:
    """The named text field contains every required substring.

    Matching is case-insensitive: a check that fails because a model
    capitalised a word is noise, and noise is what gets suites ignored.
    """

    expectation = "contains"

    def __init__(self, field: str) -> None:
        self.field = field
        self.name = f"contains_all:{field}"

    def evaluate(self, case: EvalCase, output: Any) -> CheckResult:
        required = case.expectations.get(self.expectation, ())
        text = str(_read(output, self.field) or "").lower()
        missing = [term for term in required if term.lower() not in text]
        if not missing:
            return _passed(self.name)
        # The missing terms come from the case, not from the output.
        return _failed(self.name, f"missing {missing!r}")


class TextContainsNone:
    """The named text field contains none of the forbidden substrings.

    The check that catches a regression worth catching: a reply that starts
    promising refunds, or leaking a term it should never produce.
    """

    expectation = "forbidden"

    def __init__(self, field: str) -> None:
        self.field = field
        self.name = f"contains_none:{field}"

    def evaluate(self, case: EvalCase, output: Any) -> CheckResult:
        forbidden = case.expectations.get(self.expectation, ())
        text = str(_read(output, self.field) or "").lower()
        present = [term for term in forbidden if term.lower() in text]
        if not present:
            return _passed(self.name)
        return _failed(self.name, f"contained {present!r}")


class FieldIsNotEmpty:
    """The named field is present and non-empty."""

    def __init__(self, field: str) -> None:
        self.field = field
        self.name = f"not_empty:{field}"

    def evaluate(self, case: EvalCase, output: Any) -> CheckResult:
        value = _read(output, self.field)
        if value is None or (hasattr(value, "__len__") and len(value) == 0):
            return _failed(self.name, "field was empty")
        return _passed(self.name)
