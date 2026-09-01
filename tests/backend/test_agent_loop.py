"""The agent loop, driven offline with scripted decisions."""

import logging
from typing import Any

import pytest
from pydantic import BaseModel

from app.agent.loop import (
    BUDGET_ANSWER,
    PROVIDER_ANSWER,
    AgentDecision,
    AgentLoop,
)
from app.agent.state import AgentBudget
from app.llm.base import LLMPrompt, LLMUsage, StructuredCompletion
from app.llm.errors import LLMUnavailableError
from app.tools.base import ToolError
from app.tools.execution import ToolExecutor
from app.tools.registry import ToolRegistry


class LookupParams(BaseModel):
    ticket_id: str


def make_tool(name: str, behaviour: Any) -> Any:
    class Made:
        pass

    t = Made()
    t.name = name
    t.description = f"The {name} tool."
    t.parameters = LookupParams
    t.run = behaviour
    return t


async def returns_ticket(params: LookupParams) -> dict[str, str]:
    return {"id": params.ticket_id, "status": "open"}


async def explodes(params: LookupParams) -> Any:
    raise ToolError("That ticket is archived.")


class ScriptedProvider:
    """Yields prepared decisions in order, then repeats the last one."""

    name = "scripted"

    def __init__(self, decisions: list[AgentDecision], error: Exception | None = None) -> None:
        self._decisions = decisions
        self._error = error
        self.prompts: list[LLMPrompt] = []

    async def complete_structured(self, *, prompt: LLMPrompt, schema: type, config: Any = None):
        self.prompts.append(prompt)
        if self._error is not None:
            raise self._error
        index = min(len(self.prompts) - 1, len(self._decisions) - 1)
        return StructuredCompletion[schema](
            output=self._decisions[index],
            provider=self.name,
            model="scripted",
            usage=LLMUsage(input_tokens=5, output_tokens=2),
        )


def build(
    decisions: list[AgentDecision],
    *,
    tools: list[Any] | None = None,
    budget: AgentBudget | None = None,
    error: Exception | None = None,
) -> tuple[AgentLoop, ScriptedProvider]:
    registry = ToolRegistry()
    for tool in tools or [make_tool("lookup_ticket", returns_ticket)]:
        registry.register(tool)
    provider = ScriptedProvider(decisions, error)
    return (
        AgentLoop(registry, ToolExecutor(registry), provider, budget=budget),
        provider,
    )


ANSWER_NOW = AgentDecision(final_answer="Your ticket is open.")
CALL_THEN_ANSWER = [
    AgentDecision(tool="lookup_ticket", tool_params={"ticket_id": "t-1"}),
    AgentDecision(final_answer="Your ticket is open."),
]


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_answers_without_calling_a_tool() -> None:
    loop, _ = build([ANSWER_NOW])

    outcome = await loop.run("is my ticket open")

    assert outcome.completed is True
    assert outcome.answer == "Your ticket is open."
    assert outcome.tool_calls == 0
    assert outcome.stop_reason == "answered"


@pytest.mark.anyio
async def test_calls_a_tool_then_answers() -> None:
    loop, _ = build(CALL_THEN_ANSWER)

    outcome = await loop.run("is ticket t-1 open")

    assert outcome.completed is True
    assert outcome.tool_calls == 1
    assert [s["kind"] for s in outcome.steps] == ["tool_call", "answer"]
    assert outcome.steps[0]["outcome"] == "ok"


@pytest.mark.anyio
async def test_observations_are_fed_back_into_the_next_prompt() -> None:
    """Without this the loop would call the same tool forever."""
    loop, provider = build(CALL_THEN_ANSWER)

    await loop.run("is ticket t-1 open")

    assert "already learned" in provider.prompts[1].user
    assert "lookup_ticket" in provider.prompts[1].user


@pytest.mark.anyio
async def test_tools_are_described_to_the_model_with_their_schema() -> None:
    loop, provider = build([ANSWER_NOW])

    await loop.run("q")

    assert "lookup_ticket" in provider.prompts[0].user
    assert "ticket_id" in provider.prompts[0].user


@pytest.mark.anyio
async def test_remaining_budget_is_visible_to_the_model() -> None:
    loop, provider = build([ANSWER_NOW], budget=AgentBudget(max_steps=3))

    await loop.run("q")

    assert "Steps remaining: 3" in provider.prompts[0].user


# --------------------------------------------------------------------------
# Failures end the run with an answer, never an exception
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_failing_tool_becomes_an_observation_not_a_crash() -> None:
    loop, _ = build(
        [
            AgentDecision(tool="lookup_ticket", tool_params={"ticket_id": "t-1"}),
            AgentDecision(final_answer="That ticket is archived."),
        ],
        tools=[make_tool("lookup_ticket", explodes)],
    )

    outcome = await loop.run("q")

    assert outcome.completed is True
    assert outcome.steps[0]["outcome"] == "failed"


@pytest.mark.anyio
async def test_an_unknown_tool_is_observed_and_recovered_from() -> None:
    loop, _ = build(
        [
            AgentDecision(tool="delete_everything", tool_params={}),
            AgentDecision(final_answer="I cannot do that."),
        ]
    )

    outcome = await loop.run("q")

    assert outcome.completed is True
    assert outcome.steps[0]["outcome"] == "not_found"


@pytest.mark.anyio
async def test_invalid_tool_params_are_observed() -> None:
    loop, _ = build(
        [
            AgentDecision(tool="lookup_ticket", tool_params={"wrong": 1}),
            AgentDecision(final_answer="Sorry."),
        ]
    )

    outcome = await loop.run("q")

    assert outcome.steps[0]["outcome"] == "invalid_params"


@pytest.mark.anyio
async def test_provider_failure_ends_the_run_gracefully() -> None:
    loop, _ = build([ANSWER_NOW], error=LLMUnavailableError())

    outcome = await loop.run("q")

    assert outcome.completed is False
    assert outcome.answer == PROVIDER_ANSWER
    assert outcome.stop_reason == "provider_unavailable"


# --------------------------------------------------------------------------
# Budget
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_model_that_never_answers_is_stopped_by_the_step_budget() -> None:
    """The loop must terminate even when the model will not."""
    loop, _ = build(
        [AgentDecision(tool="lookup_ticket", tool_params={"ticket_id": "t"})],
        budget=AgentBudget(max_steps=3, max_tool_calls=99),
    )

    outcome = await loop.run("q")

    assert outcome.completed is False
    assert "step limit" in outcome.stop_reason
    assert outcome.answer == BUDGET_ANSWER
    assert len(outcome.steps) == 3


@pytest.mark.anyio
async def test_the_tool_call_budget_stops_a_loop_independently() -> None:
    loop, _ = build(
        [AgentDecision(tool="lookup_ticket", tool_params={"ticket_id": "t"})],
        budget=AgentBudget(max_steps=50, max_tool_calls=2),
    )

    outcome = await loop.run("q")

    assert "tool call limit" in outcome.stop_reason
    assert outcome.tool_calls == 2


# --------------------------------------------------------------------------
# Outcome shape and logging
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_outcome_is_serialisable_and_carries_a_run_id() -> None:
    import json
    import uuid

    outcome = await (build(CALL_THEN_ANSWER))[0].run("q")

    uuid.UUID(outcome.run_id)
    json.dumps(outcome.model_dump())


@pytest.mark.anyio
async def test_transcript_excludes_tool_output() -> None:
    async def leaky(params: LookupParams) -> dict[str, str]:
        return {"body": "card 4242 charged twice"}

    loop, _ = build(CALL_THEN_ANSWER, tools=[make_tool("lookup_ticket", leaky)])

    outcome = await loop.run("q")

    assert "4242" not in str(outcome.model_dump())


@pytest.mark.anyio
async def test_logs_record_the_run_shape_not_its_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    loop, _ = build(CALL_THEN_ANSWER)

    with caplog.at_level(logging.INFO, logger="app.agent.loop"):
        await loop.run("my card 4242 was charged twice")

    assert "steps=2" in caplog.text and "tool_calls=1" in caplog.text
    assert "prompt_version=agent/v1" in caplog.text
    assert "4242" not in caplog.text
