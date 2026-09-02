"""The harness: dispatch, isolation of failures, and regression comparison."""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

import pytest

from app.evaluation.cases import EvalCase, build_suite
from app.evaluation.checks import FieldEquals, TextContainsNone
from app.evaluation.errors import EvaluationDefinitionError
from app.evaluation.results import CaseResult, CheckOutcome, CheckResult, EvalReport
from app.evaluation.runner import (
    CHECK_ERRORED,
    TARGET_FAILED,
    EvalTarget,
    EvaluationRunner,
    compare_reports,
)

pytestmark = pytest.mark.anyio


class Echo:
    """Returns the category it was given."""

    name = "echo"

    async def run(self, inputs: Mapping[str, Any]) -> Any:
        return {"category": inputs.get("category"), "answer": inputs.get("answer", "")}


class Exploding:
    name = "exploding"

    async def run(self, inputs: Mapping[str, Any]) -> Any:
        raise RuntimeError("prompt was postgresql://user:pw@host")


class Hanging:
    name = "hanging"

    async def run(self, inputs: Mapping[str, Any]) -> Any:
        await asyncio.sleep(10)


class BrokenCheck:
    name = "broken"

    def evaluate(self, case: EvalCase, output: Any) -> CheckResult:
        raise ValueError("this check is buggy")


SUITE = build_suite(
    "categories",
    [
        {
            "id": "billing",
            "inputs": {"category": "billing"},
            "expectations": {"category": "billing"},
        },
        {
            "id": "account",
            "inputs": {"category": "account"},
            "expectations": {"category": "account"},
        },
    ],
)


def passed(case_id: str) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        checks=(CheckResult(check="c", outcome=CheckOutcome.PASSED),),
    )


def failed(case_id: str) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        checks=(CheckResult(check="c", outcome=CheckOutcome.FAILED),),
    )


# --------------------------------------------------------------------------
# Construction


def test_a_runner_requires_at_least_one_check() -> None:
    """With none, every case would report as failed for a reason nobody sees."""
    with pytest.raises(EvaluationDefinitionError):
        EvaluationRunner(Echo(), [])


def test_the_echo_target_satisfies_the_protocol() -> None:
    assert isinstance(Echo(), EvalTarget)


# --------------------------------------------------------------------------
# Running


async def test_every_case_is_run_in_order() -> None:
    report = await EvaluationRunner(Echo(), [FieldEquals("category")]).run(SUITE)
    assert [r.case_id for r in report.results] == ["billing", "account"]
    assert report.passed == 2
    assert report.pass_rate == 1.0


async def test_a_failing_case_does_not_stop_the_run() -> None:
    """The value of a suite is learning about every case at once."""
    suite = build_suite(
        "mixed",
        [
            {"id": "good", "inputs": {"category": "billing"},
             "expectations": {"category": "billing"}},
            {"id": "bad", "inputs": {"category": "account"},
             "expectations": {"category": "billing"}},
            {"id": "also_good", "inputs": {"category": "billing"},
             "expectations": {"category": "billing"}},
        ],
    )
    report = await EvaluationRunner(Echo(), [FieldEquals("category")]).run(suite)
    assert report.total == 3
    assert [r.case_id for r in report.failing()] == ["bad"]


async def test_a_target_failure_is_recorded_not_raised() -> None:
    report = await EvaluationRunner(Exploding(), [FieldEquals("category")]).run(SUITE)
    assert report.passed == 0
    assert all(r.error == TARGET_FAILED for r in report.results)
    assert all(r.checks == () for r in report.results)


async def test_a_target_failure_message_never_reaches_the_report(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its text here carries a connection string, password included."""
    with caplog.at_level(logging.WARNING, logger="app.evaluation.runner"):
        report = await EvaluationRunner(Exploding(), [FieldEquals("category")]).run(SUITE)
    described = " ".join(report.describe_failures())
    assert "user:pw" not in described
    assert "user:pw" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_a_hanging_target_is_timed_out_per_case() -> None:
    runner = EvaluationRunner(Hanging(), [FieldEquals("category")], timeout_seconds=0.01)
    report = await runner.run(SUITE)
    assert report.total == 2
    assert all("0.01s" in (r.error or "") for r in report.results)


async def test_a_broken_check_is_errored_not_failed() -> None:
    """A broken harness must not be mistaken for a failing model."""
    report = await EvaluationRunner(Echo(), [BrokenCheck()]).run(SUITE)
    outcomes = {c.outcome for r in report.results for c in r.checks}
    assert outcomes == {CheckOutcome.ERRORED}
    assert all(c.detail == CHECK_ERRORED for r in report.results for c in r.checks)
    assert report.passed == 0


async def test_a_broken_check_does_not_stop_the_others() -> None:
    report = await EvaluationRunner(
        Echo(), [BrokenCheck(), FieldEquals("category")]
    ).run(SUITE)
    for result in report.results:
        assert [c.outcome for c in result.checks] == [
            CheckOutcome.ERRORED,
            CheckOutcome.PASSED,
        ]


async def test_every_check_is_applied_to_every_case() -> None:
    suite = build_suite(
        "two_checks",
        [
            {
                "id": "clean",
                "inputs": {"category": "billing", "answer": "no promises"},
                "expectations": {"category": "billing", "forbidden": ["guarantee"]},
            }
        ],
    )
    report = await EvaluationRunner(
        Echo(), [FieldEquals("category"), TextContainsNone("answer")]
    ).run(suite)
    assert [c.check for c in report.results[0].checks] == [
        "field_equals:category",
        "contains_none:answer",
    ]
    assert report.passed == 1


async def test_the_run_is_logged_by_counts_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.evaluation.runner"):
        await EvaluationRunner(Echo(), [FieldEquals("category")]).run(SUITE)
    assert "suite=categories" in caplog.text
    assert "passed=2" in caplog.text
    assert "billing" not in caplog.text


async def test_a_case_records_how_long_it_took() -> None:
    report = await EvaluationRunner(Echo(), [FieldEquals("category")]).run(SUITE)
    assert all(r.duration_ms >= 0.0 for r in report.results)


# --------------------------------------------------------------------------
# Regression comparison


def test_a_case_that_stopped_passing_is_a_regression() -> None:
    baseline = EvalReport(suite="s", results=(passed("a"), passed("b")))
    current = EvalReport(suite="s", results=(passed("a"), failed("b")))
    summary = compare_reports(baseline, current)
    assert summary.regressed == ("b",)
    assert summary.fixed == ()
    assert not summary.clean


def test_a_case_that_started_passing_is_not_a_regression() -> None:
    baseline = EvalReport(suite="s", results=(failed("a"),))
    current = EvalReport(suite="s", results=(passed("a"),))
    summary = compare_reports(baseline, current)
    assert summary.fixed == ("a",)
    assert summary.clean


def test_an_identical_pass_rate_can_still_hide_a_regression() -> None:
    """Two runs can score the same while failing on different cases."""
    baseline = EvalReport(suite="s", results=(passed("a"), failed("b")))
    current = EvalReport(suite="s", results=(failed("a"), passed("b")))
    assert baseline.pass_rate == current.pass_rate
    summary = compare_reports(baseline, current)
    assert summary.regressed == ("a",)
    assert not summary.clean


def test_a_disappearing_case_is_reported() -> None:
    """Otherwise a run that stopped exercising something looks clean."""
    baseline = EvalReport(suite="s", results=(passed("a"), passed("b")))
    current = EvalReport(suite="s", results=(passed("a"),))
    summary = compare_reports(baseline, current)
    assert summary.removed == ("b",)
    assert not summary.clean


def test_a_removed_case_alone_is_enough_to_be_unclean() -> None:
    """Isolates the removal term.

    When the dropped case was passing it also counts as a regression, so that
    check alone would hold even if removals were ignored entirely. Here the
    dropped case was already failing: nothing regressed, and the only thing
    wrong is that the suite stopped asking.
    """
    baseline = EvalReport(suite="s", results=(passed("a"), failed("b")))
    current = EvalReport(suite="s", results=(passed("a"),))
    summary = compare_reports(baseline, current)
    assert summary.regressed == ()
    assert summary.removed == ("b",)
    assert not summary.clean


def test_a_new_case_is_reported_but_is_not_a_regression() -> None:
    baseline = EvalReport(suite="s", results=(passed("a"),))
    current = EvalReport(suite="s", results=(passed("a"), failed("b")))
    summary = compare_reports(baseline, current)
    assert summary.added == ("b",)
    assert summary.regressed == ()
    assert summary.clean


def test_reports_from_different_suites_are_not_comparable() -> None:
    with pytest.raises(EvaluationDefinitionError):
        compare_reports(
            EvalReport(suite="one", results=()), EvalReport(suite="two", results=())
        )


def test_the_comparison_summarises_by_counts() -> None:
    baseline = EvalReport(suite="s", results=(passed("a"), passed("b")))
    current = EvalReport(suite="s", results=(passed("a"), failed("b")))
    assert compare_reports(baseline, current).summary() == (
        "suite=s regressed=1 fixed=0 added=0 removed=0"
    )
