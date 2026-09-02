"""Cases, checks, and reports — the pieces the harness is assembled from."""

import pytest

from app.evaluation.cases import EvalCase, build_suite
from app.evaluation.checks import (
    Check,
    FieldEquals,
    FieldIn,
    FieldIsNotEmpty,
    TextContainsAll,
    TextContainsNone,
)
from app.evaluation.errors import EvaluationDefinitionError
from app.evaluation.results import CaseResult, CheckOutcome, CheckResult, EvalReport


def case(**kwargs: object) -> EvalCase:
    return EvalCase(id=kwargs.pop("id", "c1"), **kwargs)  # type: ignore[arg-type]


def passed(name: str = "c") -> CheckResult:
    return CheckResult(check=name, outcome=CheckOutcome.PASSED)


def failed(name: str = "c") -> CheckResult:
    return CheckResult(check=name, outcome=CheckOutcome.FAILED, detail="nope")


# --------------------------------------------------------------------------
# Cases and suites


def test_a_suite_rejects_duplicate_ids() -> None:
    """A report that names a case twice is ambiguous exactly when it matters."""
    with pytest.raises(EvaluationDefinitionError):
        build_suite("s", [{"id": "a"}, {"id": "a"}])


def test_a_suite_rejects_being_empty() -> None:
    with pytest.raises(EvaluationDefinitionError):
        build_suite("s", [])


def test_a_suite_accepts_mappings_and_cases() -> None:
    suite = build_suite("s", [{"id": "a"}, EvalCase(id="b")])
    assert len(suite) == 2
    assert [c.id for c in suite.cases] == ["a", "b"]


def test_cases_can_be_selected_by_tag() -> None:
    suite = build_suite(
        "s",
        [
            {"id": "a", "tags": ("billing",)},
            {"id": "b", "tags": ("technical",)},
            {"id": "c", "tags": ("billing", "slow")},
        ],
    )
    assert [c.id for c in suite.tagged("billing")] == ["a", "c"]
    assert suite.tagged("absent") == ()


def test_a_case_is_frozen() -> None:
    with pytest.raises(Exception):
        case().id = "changed"  # type: ignore[misc]


def test_a_case_needs_an_id() -> None:
    with pytest.raises(Exception):
        EvalCase(id="")


# --------------------------------------------------------------------------
# Checks


def test_every_check_satisfies_the_protocol() -> None:
    for check in (
        FieldEquals("category"),
        FieldIn("category", ["a"]),
        TextContainsAll("answer"),
        TextContainsNone("answer"),
        FieldIsNotEmpty("answer"),
    ):
        assert isinstance(check, Check)


def test_field_equals_passes_and_fails() -> None:
    check = FieldEquals("category")
    subject = case(expectations={"category": "billing"})
    assert check.evaluate(subject, {"category": "billing"}).ok
    assert not check.evaluate(subject, {"category": "account"}).ok


def test_field_equals_reads_objects_as_well_as_mappings() -> None:
    class Output:
        category = "billing"

    assert FieldEquals("category").evaluate(
        case(expectations={"category": "billing"}), Output()
    ).ok


def test_field_equals_fails_when_the_case_declares_no_expectation() -> None:
    """Silently passing an undeclared expectation is how a suite goes hollow."""
    result = FieldEquals("category").evaluate(case(), {"category": "billing"})
    assert not result.ok
    assert "declares no" in result.detail


def test_field_equals_can_read_a_differently_named_expectation() -> None:
    check = FieldEquals("category", expectation="expected_category")
    subject = case(expectations={"expected_category": "billing"})
    assert check.evaluate(subject, {"category": "billing"}).ok


def test_field_in_accepts_any_allowed_value() -> None:
    check = FieldIn("category", ["billing", "account"])
    assert check.evaluate(case(), {"category": "account"}).ok
    assert not check.evaluate(case(), {"category": "other"}).ok


def test_field_in_rejects_an_empty_allowed_set() -> None:
    with pytest.raises(EvaluationDefinitionError):
        FieldIn("category", [])


def test_contains_all_is_case_insensitive() -> None:
    check = TextContainsAll("answer")
    subject = case(expectations={"contains": ["Refund", "five days"]})
    assert check.evaluate(subject, {"answer": "your REFUND takes Five Days"}).ok


def test_contains_all_names_what_was_missing() -> None:
    check = TextContainsAll("answer")
    subject = case(expectations={"contains": ["refund", "invoice"]})
    result = check.evaluate(subject, {"answer": "about your refund"})
    assert not result.ok
    assert "invoice" in result.detail


def test_contains_all_passes_when_nothing_is_required() -> None:
    assert TextContainsAll("answer").evaluate(case(), {"answer": "anything"}).ok


def test_contains_none_catches_a_forbidden_term() -> None:
    check = TextContainsNone("answer")
    subject = case(expectations={"forbidden": ["guarantee"]})
    assert check.evaluate(subject, {"answer": "no promises here"}).ok
    result = check.evaluate(subject, {"answer": "I GUARANTEE a refund"})
    assert not result.ok
    assert "guarantee" in result.detail


def test_contains_none_handles_a_missing_field() -> None:
    subject = case(expectations={"forbidden": ["guarantee"]})
    assert TextContainsNone("answer").evaluate(subject, {}).ok


def test_not_empty_rejects_absent_and_empty_values() -> None:
    check = FieldIsNotEmpty("answer")
    assert check.evaluate(case(), {"answer": "something"}).ok
    assert not check.evaluate(case(), {"answer": ""}).ok
    assert not check.evaluate(case(), {"answer": []}).ok
    assert not check.evaluate(case(), {}).ok


# --------------------------------------------------------------------------
# Results


def test_a_case_passes_only_when_every_check_did() -> None:
    """Any-of would let a suite go green while the check that mattered failed."""
    assert CaseResult(case_id="a", checks=(passed(), passed())).passed
    assert not CaseResult(case_id="a", checks=(passed(), failed())).passed


def test_a_case_with_no_checks_has_not_passed() -> None:
    """Nothing was verified, so nothing may be claimed."""
    assert not CaseResult(case_id="a").passed


def test_a_target_error_fails_the_case() -> None:
    result = CaseResult(case_id="a", checks=(passed(),), error="target exploded")
    assert not result.passed


def test_failures_are_listed() -> None:
    result = CaseResult(case_id="a", checks=(passed("ok"), failed("bad")))
    assert [c.check for c in result.failures] == ["bad"]


def test_a_report_counts_and_rates() -> None:
    report = EvalReport(
        suite="s",
        results=(
            CaseResult(case_id="a", checks=(passed(),)),
            CaseResult(case_id="b", checks=(failed(),)),
            CaseResult(case_id="c", checks=(passed(),)),
        ),
    )
    assert (report.total, report.passed, report.failed) == (3, 2, 1)
    assert report.pass_rate == pytest.approx(2 / 3)
    assert [r.case_id for r in report.failing()] == ["b"]


def test_an_empty_report_is_not_a_total_failure() -> None:
    """Nothing was wrong with a run that had nothing to do."""
    assert EvalReport(suite="s", results=()).pass_rate == 1.0


def test_the_summary_carries_counts_only() -> None:
    report = EvalReport(
        suite="intent", results=(CaseResult(case_id="a", checks=(passed(),)),)
    )
    assert report.summary() == (
        "suite=intent total=1 passed=1 failed=0 pass_rate=1.00"
    )


def test_failures_are_described_by_case_and_check() -> None:
    report = EvalReport(
        suite="s",
        results=(
            CaseResult(case_id="a", checks=(failed("field_equals:category"),)),
            CaseResult(case_id="b", error="target exploded"),
        ),
    )
    described = list(report.describe_failures())
    assert described == [
        "a: field_equals:category — nope",
        "b: target exploded",
    ]
