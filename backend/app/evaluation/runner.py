"""Running a suite.

The harness is the boundary where anything may go wrong and nothing may
escape — the third place in this codebase with that rule, after the tool
executor and the job worker, and for the same reason each time. A run that
stops at the first exception tells you about one case; the entire value of a
suite is learning about all of them at once.

What it will not do is hide a failure. A target that raised, a check that
raised, and a check that returned "failed" are three different outcomes and
stay three different outcomes in the report. Collapsing them would make a
broken harness look like a failing model.

The runner is provider-agnostic: it drives an ``EvalTarget``, which is
whatever the suite is about — a prompt, a workflow, a handler. That is what
lets one harness evaluate all three without knowing anything about any of them.
"""

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.evaluation.cases import EvalCase, EvalSuite
from app.evaluation.checks import Check
from app.evaluation.errors import EvaluationDefinitionError
from app.evaluation.results import CaseResult, CheckOutcome, CheckResult, EvalReport

logger = get_logger(__name__)

DEFAULT_CASE_TIMEOUT_SECONDS = 30.0

TARGET_FAILED = "The target failed to produce an output."
CHECK_ERRORED = "The check itself raised."


@runtime_checkable
class EvalTarget(Protocol):
    """The thing being evaluated."""

    name: str

    async def run(self, inputs: Mapping[str, Any]) -> Any:
        """Produce an output for one case's inputs."""
        ...


class EvaluationRunner:
    """Runs every case in a suite and reports what happened."""

    def __init__(
        self,
        target: EvalTarget,
        checks: Sequence[Check],
        *,
        timeout_seconds: float = DEFAULT_CASE_TIMEOUT_SECONDS,
    ) -> None:
        if not checks:
            # A run with no checks would report every case as failed, since a
            # case with no checks has verified nothing. Refusing here says why.
            raise EvaluationDefinitionError(
                "At least one check is required.",
                details={"target": getattr(target, "name", "unknown")},
            )
        self._target = target
        self._checks = tuple(checks)
        self._timeout = timeout_seconds

    async def run(self, suite: EvalSuite) -> EvalReport:
        """Evaluate every case, in order."""
        results = [await self._run_case(case) for case in suite.cases]
        report = EvalReport(suite=suite.name, results=tuple(results))
        # Counts only. Case inputs and model output are both content.
        logger.info("evaluation complete %s", report.summary())
        return report

    async def _run_case(self, case: EvalCase) -> CaseResult:
        started = time.perf_counter()

        try:
            async with asyncio.timeout(self._timeout):
                output = await self._target.run(case.inputs)
        except TimeoutError:
            return self._errored(
                case, f"The target did not finish within {self._timeout:g}s.", started
            )
        except Exception as exc:
            # Type only. A target failure can carry a prompt, a row, or a
            # connection string in its message.
            logger.warning(
                "evaluation target failed case=%s error=%s",
                case.id,
                type(exc).__name__,
            )
            return self._errored(case, TARGET_FAILED, started)

        return CaseResult(
            case_id=case.id,
            checks=tuple(self._check(check, case, output) for check in self._checks),
            duration_ms=_elapsed_ms(started),
        )

    def _check(self, check: Check, case: EvalCase, output: Any) -> CheckResult:
        """Apply one check, treating a raising check as its own outcome."""
        try:
            return check.evaluate(case, output)
        except Exception as exc:
            # Distinct from "failed": a broken harness must not be mistaken
            # for a failing model.
            logger.warning(
                "evaluation check raised case=%s check=%s error=%s",
                case.id,
                getattr(check, "name", "unknown"),
                type(exc).__name__,
            )
            return CheckResult(
                check=getattr(check, "name", "unknown"),
                outcome=CheckOutcome.ERRORED,
                detail=CHECK_ERRORED,
            )

    def _errored(self, case: EvalCase, error: str, started: float) -> CaseResult:
        return CaseResult(
            case_id=case.id, error=error, duration_ms=_elapsed_ms(started)
        )


class RegressionSummary:
    """What changed between two runs of the same suite.

    A pass rate alone cannot answer the question that matters when a prompt
    changes -- "did anything that used to work stop working" -- because two
    runs can score identically while failing on different cases.
    """

    def __init__(self, baseline: EvalReport, current: EvalReport) -> None:
        base_pass = {r.case_id for r in baseline.results if r.passed}
        now_pass = {r.case_id for r in current.results if r.passed}
        base_ids = {r.case_id for r in baseline.results}
        now_ids = {r.case_id for r in current.results}

        self.baseline = baseline
        self.current = current
        self.regressed = tuple(sorted(base_pass - now_pass))
        self.fixed = tuple(sorted(now_pass - base_pass))
        # Cases present in one run only. Reported rather than ignored: a
        # comparison that quietly drops them can show "no regressions" for a
        # run that simply stopped exercising something.
        self.added = tuple(sorted(now_ids - base_ids))
        self.removed = tuple(sorted(base_ids - now_ids))

    @property
    def clean(self) -> bool:
        """Nothing that used to pass now fails, and nothing went missing."""
        return not self.regressed and not self.removed

    def summary(self) -> str:
        return (
            f"suite={self.current.suite} regressed={len(self.regressed)} "
            f"fixed={len(self.fixed)} added={len(self.added)} "
            f"removed={len(self.removed)}"
        )


def compare_reports(baseline: EvalReport, current: EvalReport) -> RegressionSummary:
    """Compare two runs of the same suite."""
    if baseline.suite != current.suite:
        raise EvaluationDefinitionError(
            "Reports from different suites are not comparable.",
            details={"baseline": baseline.suite, "current": current.suite},
        )
    return RegressionSummary(baseline, current)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
