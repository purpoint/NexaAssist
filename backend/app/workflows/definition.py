"""The workflow definition format.

A workflow is an ordered list of steps. Each step names a tool from the M6
registry and supplies its parameters, which may reference earlier results.

Deliberately declarative and inert: a definition can be read, validated, and
compared without running anything. Everything that could vary at runtime is a
reference resolved at execution time, not code embedded in the definition.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_STEPS = 20
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

REFERENCE = re.compile(r"^\{\{\s*steps\.([a-z][a-z0-9_]*)\.output\s*\}\}$")
"""A parameter value that is entirely a reference to an earlier step's output.

Only whole-value references are supported, not interpolation into a larger
string. Substituting a structured result into the middle of a string would
force a stringification whose format nothing has agreed on.
"""


class WorkflowStep(BaseModel):
    """One tool call in a workflow."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Unique within the workflow; used to reference output.")
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    optional: bool = Field(
        default=False,
        description=(
            "When true a failure is recorded and the workflow continues. "
            "Otherwise a failed step ends the run."
        ),
    )

    @model_validator(mode="after")
    def _validate_names(self) -> "WorkflowStep":
        if not NAME_PATTERN.match(self.id):
            raise ValueError(
                f"Step id {self.id!r} must be lowercase alphanumeric with underscores."
            )
        if not NAME_PATTERN.match(self.tool):
            raise ValueError(f"Tool name {self.tool!r} is not a valid tool name.")
        return self

    def references(self) -> set[str]:
        """Step ids this step's parameters depend on."""
        found: set[str] = set()
        for value in self.params.values():
            if isinstance(value, str):
                match = REFERENCE.match(value)
                if match:
                    found.add(match.group(1))
        return found


class Workflow(BaseModel):
    """A named, ordered sequence of steps."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = Field(min_length=1)
    inputs: list[str] = Field(
        default_factory=list,
        description=(
            "Names the caller must supply at run time. Declared rather than "
            "implicit so a reference to an input is validated like any other, "
            "and so a workflow documents what it needs."
        ),
    )
    steps: list[WorkflowStep] = Field(min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _validate(self) -> "Workflow":
        if not NAME_PATTERN.match(self.name):
            raise ValueError(f"Workflow name {self.name!r} must be a valid identifier.")

        for name in self.inputs:
            if not NAME_PATTERN.match(name):
                raise ValueError(f"Input name {name!r} must be a valid identifier.")

        # Declared inputs are available to every step, so they seed the set of
        # names a reference may legally resolve to.
        seen: set[str] = set(self.inputs)
        for step in self.steps:
            if step.id in seen:
                # Duplicate ids -- or an id shadowing an input -- would make a
                # reference ambiguous.
                raise ValueError(f"Duplicate step id {step.id!r}.")
            # References are checked against steps already seen, which also
            # rules out forward and self references -- a workflow is a
            # sequence, not a graph, and a cycle could never terminate.
            unknown = step.references() - seen
            if unknown:
                raise ValueError(
                    f"Step {step.id!r} references {sorted(unknown)}, which do not "
                    "appear before it."
                )
            seen.add(step.id)
        return self

    def step_ids(self) -> list[str]:
        return [step.id for step in self.steps]

    def tools_used(self) -> list[str]:
        """Distinct tool names, in first-use order."""
        seen: dict[str, None] = {}
        for step in self.steps:
            seen.setdefault(step.tool, None)
        return list(seen)


def reference_to(step_id: str) -> str:
    """Build a reference to ``step_id``'s output."""
    return f"{{{{ steps.{step_id}.output }}}}"


def referenced_step(value: object) -> str | None:
    """The step id a value refers to, or ``None`` if it is a literal."""
    if not isinstance(value, str):
        return None
    match = REFERENCE.match(value)
    return match.group(1) if match else None
