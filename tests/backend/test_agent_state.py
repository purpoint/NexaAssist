"""Agent state and step accounting. Offline."""

import uuid

import pytest

from app.agent.state import (
    DEFAULT_MAX_STEPS,
    AgentBudget,
    AgentState,
    AgentStep,
    BudgetExhausted,
    StepKind,
)
from app.llm.base import LLMUsage
from app.tools.results import ToolOutcome, ToolResult


def ok(tool: str = "lookup_ticket", ms: float = 5.0) -> ToolResult:
    return ToolResult.success(tool, {"id": "x"}, duration_ms=ms)


def bad(tool: str = "lookup_ticket") -> ToolResult:
    return ToolResult.failure(tool, ToolOutcome.FAILED, "nope")


# --------------------------------------------------------------------------
# Identity and defaults
# --------------------------------------------------------------------------


def test_a_run_has_an_identity_and_starts_empty() -> None:
    state = AgentState("why was I charged twice")

    assert isinstance(state.run_id, uuid.UUID)
    assert state.question == "why was I charged twice"
    assert state.steps == []
    assert state.step_count == 0
    assert state.usage.input_tokens == 0


def test_default_budget_is_applied() -> None:
    assert AgentState("q").budget.max_steps == DEFAULT_MAX_STEPS


def test_runs_have_distinct_identities() -> None:
    assert AgentState("q").run_id != AgentState("q").run_id


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------


def test_tool_calls_are_recorded_in_order() -> None:
    state = AgentState("q")
    state.record_tool_call("a", ok("a"))
    state.record_tool_call("b", ok("b"))

    assert [s.index for s in state.steps] == [0, 1]
    assert [s.tool for s in state.steps] == ["a", "b"]
    assert all(s.kind is StepKind.TOOL_CALL for s in state.steps)


def test_answer_steps_are_recorded() -> None:
    state = AgentState("q")
    state.record_answer(duration_ms=12.0)

    assert state.steps[0].kind is StepKind.ANSWER
    assert state.steps[0].tool is None


def test_recorded_steps_are_immutable() -> None:
    """History must not be rewritten after the fact."""
    state = AgentState("q")
    step = state.record_tool_call("a", ok())

    with pytest.raises(Exception):
        step.index = 99


def test_step_duration_comes_from_the_tool_result() -> None:
    state = AgentState("q")
    state.record_tool_call("a", ok(ms=42.0))

    assert state.steps[0].duration_ms == 42.0


# --------------------------------------------------------------------------
# Accounting
# --------------------------------------------------------------------------


def test_counts_distinguish_steps_tool_calls_and_successes() -> None:
    state = AgentState("q")
    state.record_tool_call("a", ok("a"))
    state.record_tool_call("b", bad("b"))
    state.record_answer()

    assert state.step_count == 3
    assert state.tool_calls == 2
    assert state.successful_tool_calls == 1


def test_usage_accumulates_across_steps() -> None:
    state = AgentState("q")
    state.record_tool_call("a", ok(), usage=LLMUsage(input_tokens=10, output_tokens=3))
    state.record_answer(usage=LLMUsage(input_tokens=5, output_tokens=7))

    assert state.usage.input_tokens == 15
    assert state.usage.output_tokens == 10


def test_duration_accumulates() -> None:
    state = AgentState("q")
    state.record_tool_call("a", ok(ms=10.0))
    state.record_answer(duration_ms=5.0)

    assert state.duration_ms == pytest.approx(15.0)


def test_tools_used_is_deduplicated_and_ordered_by_first_use() -> None:
    state = AgentState("q")
    for tool in ("search", "lookup", "search"):
        state.record_tool_call(tool, ok(tool))

    assert state.tools_used() == ["search", "lookup"]


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


def test_remaining_counts_decrease() -> None:
    state = AgentState("q", AgentBudget(max_steps=3, max_tool_calls=2))
    state.record_tool_call("a", ok())

    assert state.remaining_steps() == 2
    assert state.remaining_tool_calls() == 1


def test_step_limit_stops_the_run() -> None:
    state = AgentState("q", AgentBudget(max_steps=1, max_tool_calls=9))
    state.record_answer()

    assert "step limit" in state.exhausted()
    with pytest.raises(BudgetExhausted, match="step limit"):
        state.ensure_within_budget()


def test_tool_call_limit_stops_the_run_independently_of_steps() -> None:
    """Bounding only steps would leave tool calls unbounded, and vice versa."""
    state = AgentState("q", AgentBudget(max_steps=10, max_tool_calls=1))
    state.record_tool_call("a", ok())

    assert "tool call limit" in state.exhausted()


def test_a_fresh_run_is_within_budget() -> None:
    state = AgentState("q")

    assert state.exhausted() is None
    state.ensure_within_budget()


def test_remaining_never_goes_negative() -> None:
    state = AgentState("q", AgentBudget(max_steps=1, max_tool_calls=0))
    state.record_answer()
    state.record_answer()

    assert state.remaining_steps() == 0
    assert state.remaining_tool_calls() == 0


@pytest.mark.parametrize(
    "kwargs", [{"max_steps": 0}, {"max_steps": 51}, {"max_tool_calls": -1}]
)
def test_nonsensical_budgets_are_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        AgentBudget(**kwargs)


def test_zero_tool_calls_is_a_valid_budget() -> None:
    """An answer-only run is legitimate."""
    assert AgentBudget(max_tool_calls=0).max_tool_calls == 0


# --------------------------------------------------------------------------
# Transcript
# --------------------------------------------------------------------------


def test_transcript_summarises_each_step() -> None:
    state = AgentState("q")
    state.record_tool_call("lookup", ok("lookup", ms=3.0))
    state.record_answer()

    transcript = state.transcript()

    assert [entry["kind"] for entry in transcript] == ["tool_call", "answer"]
    assert transcript[0]["tool"] == "lookup"
    assert transcript[0]["outcome"] == "ok"
    assert transcript[1]["outcome"] is None


def test_transcript_excludes_tool_output() -> None:
    """A transcript may be logged or returned; outputs carry customer data."""
    state = AgentState("q")
    state.record_tool_call(
        "lookup", ToolResult.success("lookup", {"body": "card 4242 charged twice"})
    )

    assert "4242" not in str(state.transcript())


def test_transcript_is_serialisable() -> None:
    import json

    state = AgentState("q")
    state.record_tool_call("lookup", ok())

    json.dumps(state.transcript())


def test_step_index_must_be_non_negative() -> None:
    with pytest.raises(ValueError):
        AgentStep(index=-1, kind=StepKind.ANSWER)
