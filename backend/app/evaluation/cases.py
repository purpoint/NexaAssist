"""What an evaluation case is.

A case is an input plus what should be true of the output — never the output
itself. Recording a whole expected response would make the suite a change
detector: any reword fails it, so it gets updated until nobody reads the diff
any more and it stops detecting anything.

Cases are data, and the checks in :mod:`app.evaluation.checks` are what turn
them into a verdict. Keeping the two apart is what lets one case be judged by
several checks, and one check be reused across suites.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.evaluation.errors import EvaluationDefinitionError


class EvalCase(BaseModel):
    """One input, and what should hold of the result."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, description="Stable, so a failure names something.")
    inputs: dict[str, Any] = Field(default_factory=dict)
    expectations: dict[str, Any] = Field(default_factory=dict)
    tags: tuple[str, ...] = Field(
        default=(), description="For selecting a subset, e.g. 'billing' or 'slow'."
    )


class EvalSuite(BaseModel):
    """A named set of cases, evaluated together."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    cases: tuple[EvalCase, ...]

    def model_post_init(self, _context: object) -> None:
        """Reject duplicate ids at construction.

        Two cases sharing an id makes a report ambiguous exactly when it
        matters — while somebody is reading which case failed.
        """
        seen: set[str] = set()
        duplicates = sorted({c.id for c in self.cases if c.id in seen or seen.add(c.id)})
        if duplicates:
            raise EvaluationDefinitionError(
                "Case ids must be unique within a suite.",
                details={"suite": self.name, "duplicates": duplicates},
            )
        if not self.cases:
            raise EvaluationDefinitionError(
                "A suite must contain at least one case.",
                details={"suite": self.name},
            )

    def tagged(self, tag: str) -> tuple[EvalCase, ...]:
        """The cases carrying ``tag``, in their original order."""
        return tuple(case for case in self.cases if tag in case.tags)

    def __len__(self) -> int:
        return len(self.cases)


def build_suite(
    name: str, cases: Sequence[Mapping[str, Any] | EvalCase]
) -> EvalSuite:
    """Construct a suite from plain mappings or ready-made cases."""
    return EvalSuite(
        name=name,
        cases=tuple(
            case if isinstance(case, EvalCase) else EvalCase(**dict(case))
            for case in cases
        ),
    )
