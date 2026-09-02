"""What an evaluation produces.

Always a report, never an exception — the same rule the tool executor and the
job worker follow, for the same reason. A harness that stops at the first
failure tells you about one case; the value of running a suite is knowing about
all of them.

A report is deliberately more than a score. A single number cannot tell a run
where two unrelated things broke from a run where one did, and "which cases,
and why" is what somebody actually acts on.
"""

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CheckOutcome(StrEnum):
    """How one check ended."""

    PASSED = "passed"
    FAILED = "failed"
    ERRORED = "errored"


class CheckResult(BaseModel):
    """One check's verdict on one case."""

    model_config = ConfigDict(frozen=True)

    check: str
    outcome: CheckOutcome
    detail: str | None = Field(
        default=None,
        description=(
            "Why, phrased for a person reading a failure. Never a traceback "
            "and never the model's full output."
        ),
    )

    @property
    def ok(self) -> bool:
        return self.outcome is CheckOutcome.PASSED


class CaseResult(BaseModel):
    """Every check's verdict on one case."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    checks: tuple[CheckResult, ...] = ()
    error: str | None = Field(
        default=None,
        description="Set when the target itself failed, so no check could run.",
    )
    duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        """A case passes only if every check did.

        Any-of would let a suite go green while the check that mattered failed.
        """
        return self.error is None and bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.ok)


class EvalReport(BaseModel):
    """The outcome of running a suite."""

    model_config = ConfigDict(frozen=True)

    suite: str
    results: tuple[CaseResult, ...]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        """Passed over total, or 1.0 for an empty run.

        An empty suite has nothing wrong with it. Returning 0.0 would report a
        vacuous run as a total failure, which is the opposite of what happened.
        """
        return 1.0 if self.total == 0 else self.passed / self.total

    def failing(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if not result.passed)

    def summary(self) -> str:
        """One line for a console or a log. Counts only, never case content."""
        return (
            f"suite={self.suite} total={self.total} passed={self.passed} "
            f"failed={self.failed} pass_rate={self.pass_rate:.2f}"
        )

    def describe_failures(self) -> Sequence[str]:
        """Case id and check names, so a failure names something actionable."""
        lines = []
        for result in self.failing():
            if result.error is not None:
                lines.append(f"{result.case_id}: {result.error}")
                continue
            for check in result.failures:
                detail = f" — {check.detail}" if check.detail else ""
                lines.append(f"{result.case_id}: {check.check}{detail}")
        return lines
