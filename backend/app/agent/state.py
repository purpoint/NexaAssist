"""Agent state and step accounting.

An agent run is a sequence of steps, and every step is recorded: what was
attempted, what came back, how long it took, and what it cost. Without that
record a failed run is unexplainable after the fact, and a runaway one is
unbounded.

The budget is enforced here rather than inside the loop so that "how much may
this run spend" is answerable by reading one small object.
"""

import uuid
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.llm.base import LLMUsage
from app.tools.results import ToolResult

DEFAULT_MAX_STEPS = 6
DEFAULT_MAX_TOOL_CALLS = 8


class StepKind(StrEnum):
    """What a step did."""

    TOOL_CALL = "tool_call"
    ANSWER = "answer"


class AgentStep(BaseModel):
    """One recorded action in a run."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    kind: StepKind
    tool: str | None = None
    result: ToolResult | None = None
    usage: LLMUsage = Field(default_factory=LLMUsage)
    duration_ms: float = 0.0


class AgentBudget(BaseModel):
    """What a single run is allowed to spend.

    Two limits rather than one: a loop can burn steps without calling a tool
    (the model answering, then reconsidering), and it can call tools far more
    often than it takes steps if a step ever fans out. Bounding only one of
    them leaves the other unbounded.
    """

    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=50)
    max_tool_calls: int = Field(default=DEFAULT_MAX_TOOL_CALLS, ge=0, le=100)


class BudgetExhausted(Exception):
    """The run reached a limit and must stop.

    Not an application error: the loop catches it and finishes with whatever it
    has. A half-finished run is a result, not a server fault.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AgentState:
    """Everything one run accumulates.

    Mutable by design -- it is the run's ledger -- but each recorded step is
    frozen, so history cannot be rewritten after the fact.
    """

    def __init__(self, question: str, budget: AgentBudget | None = None) -> None:
        self.run_id = uuid.uuid4()
        self.question = question
        self.budget = budget or AgentBudget()
        self.steps: list[AgentStep] = []
        self.usage = LLMUsage()

    # -- accounting ------------------------------------------------------

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def tool_calls(self) -> int:
        return sum(1 for step in self.steps if step.kind is StepKind.TOOL_CALL)

    @property
    def successful_tool_calls(self) -> int:
        return sum(
            1
            for step in self.steps
            if step.kind is StepKind.TOOL_CALL and step.result is not None and step.result.ok
        )

    @property
    def duration_ms(self) -> float:
        return sum(step.duration_ms for step in self.steps)

    def tools_used(self) -> list[str]:
        """Distinct tool names, in the order first called."""
        seen: dict[str, None] = {}
        for step in self.steps:
            if step.tool is not None:
                seen.setdefault(step.tool, None)
        return list(seen)

    # -- budget ----------------------------------------------------------

    def remaining_steps(self) -> int:
        return max(0, self.budget.max_steps - self.step_count)

    def remaining_tool_calls(self) -> int:
        return max(0, self.budget.max_tool_calls - self.tool_calls)

    def exhausted(self) -> str | None:
        """Why the run must stop, or ``None`` if it may continue."""
        if self.remaining_steps() == 0:
            return f"step limit of {self.budget.max_steps} reached"
        if self.remaining_tool_calls() == 0:
            return f"tool call limit of {self.budget.max_tool_calls} reached"
        return None

    def ensure_within_budget(self) -> None:
        reason = self.exhausted()
        if reason is not None:
            raise BudgetExhausted(reason)

    # -- recording -------------------------------------------------------

    def record_tool_call(
        self, tool: str, result: ToolResult, *, usage: LLMUsage | None = None
    ) -> AgentStep:
        return self._append(
            AgentStep(
                index=self.step_count,
                kind=StepKind.TOOL_CALL,
                tool=tool,
                result=result,
                usage=usage or LLMUsage(),
                duration_ms=result.duration_ms,
            )
        )

    def record_answer(
        self, *, usage: LLMUsage | None = None, duration_ms: float = 0.0
    ) -> AgentStep:
        return self._append(
            AgentStep(
                index=self.step_count,
                kind=StepKind.ANSWER,
                usage=usage or LLMUsage(),
                duration_ms=duration_ms,
            )
        )

    def _append(self, step: AgentStep) -> AgentStep:
        self.steps.append(step)
        self.usage = LLMUsage(
            input_tokens=self.usage.input_tokens + step.usage.input_tokens,
            output_tokens=self.usage.output_tokens + step.usage.output_tokens,
        )
        return step

    def transcript(self) -> list[dict[str, object]]:
        """A serialisable summary of the run.

        Deliberately excludes tool *output*: a transcript may be logged or
        returned, and outputs carry customer content.
        """
        return [
            {
                "index": step.index,
                "kind": step.kind.value,
                "tool": step.tool,
                "outcome": step.result.outcome.value if step.result else None,
                "duration_ms": round(step.duration_ms, 1),
            }
            for step in self.steps
        ]
