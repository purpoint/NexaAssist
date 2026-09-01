"""The agent loop.

Decide, act, observe, repeat -- over the M6 tool system and the M2 provider
protocol. It knows nothing about Groq or FastAPI, and nothing about which
agent to run for a given request; that is routing, and belongs to M8.

The loop is deliberately small. Everything that could run away is bounded by
:class:`~app.agent.state.AgentBudget`, and every failure -- a bad tool name,
invalid parameters, a timeout, a provider outage -- ends the run with an answer
rather than an exception, because a support request that dies with a traceback
helps nobody.
"""

import time
from typing import Any

from pydantic import BaseModel, Field

from app.agent.state import AgentBudget, AgentState, BudgetExhausted
from app.core.logging import get_logger
from app.llm.base import LLMPrompt, LLMProvider
from app.llm.errors import LLMError
from app.llm.prompts import AGENT_PROMPT_VERSION, AGENT_SYSTEM_PROMPT
from app.tools.execution import ToolExecutor
from app.tools.registry import ToolRegistry

logger = get_logger(__name__)

BUDGET_ANSWER = (
    "I could not finish looking into that within the allowed number of steps."
)
PROVIDER_ANSWER = "I could not complete that request because a service was unavailable."


class AgentDecision(BaseModel):
    """What the model wants to do next."""

    reasoning: str = Field(
        default="", max_length=500, description="Why this step was chosen."
    )
    tool: str | None = Field(
        default=None, description="Tool to call, or null to answer now."
    )
    tool_params: dict[str, Any] = Field(default_factory=dict)
    final_answer: str | None = Field(
        default=None, max_length=2_000, description="The answer, when done."
    )


class AgentOutcome(BaseModel):
    """The result of a whole run."""

    run_id: str
    question: str
    answer: str
    completed: bool = Field(
        description="False when the run stopped on budget or a provider failure."
    )
    steps: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: int = 0
    stop_reason: str = "answered"


class AgentLoop:
    """Runs one question to an answer, bounded and recorded."""

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        provider: LLMProvider,
        *,
        budget: AgentBudget | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._provider = provider
        self._budget = budget or AgentBudget()

    async def run(self, question: str) -> AgentOutcome:
        state = AgentState(question, self._budget)

        while True:
            try:
                state.ensure_within_budget()
            except BudgetExhausted as exc:
                return self._finish(state, BUDGET_ANSWER, completed=False, stop_reason=exc.reason)

            started = time.perf_counter()
            try:
                decision = await self._decide(state)
            except LLMError:
                # The provider layer already classified and logged this. A run
                # that cannot think is over; it is not a server error.
                logger.warning("agent run halted run_id=%s reason=provider", state.run_id)
                return self._finish(
                    state, PROVIDER_ANSWER, completed=False, stop_reason="provider_unavailable"
                )
            elapsed = (time.perf_counter() - started) * 1000.0

            if decision.tool is None:
                state.record_answer(duration_ms=elapsed)
                answer = decision.final_answer or BUDGET_ANSWER
                return self._finish(state, answer, completed=True, stop_reason="answered")

            # A tool step. The executor never raises, so a bad name, invalid
            # parameters, or a timeout simply becomes an observation the model
            # can react to on the next turn.
            result = await self._executor.execute(decision.tool, decision.tool_params)
            state.record_tool_call(decision.tool, result)

    async def _decide(self, state: AgentState) -> AgentDecision:
        completion = await self._provider.complete_structured(
            prompt=LLMPrompt(
                system=AGENT_SYSTEM_PROMPT, user=self._render(state)
            ),
            schema=AgentDecision,
        )
        return completion.output

    def _render(self, state: AgentState) -> str:
        tools = "\n".join(
            f"- {described['name']}: {described['description']}\n"
            f"  parameters: {described['parameters']}"
            for described in self._registry.describe_all()
        )
        observations = "\n".join(
            f"[{step.index}] {step.tool} -> {step.result.outcome.value}: "
            f"{step.result.output if step.result.ok else step.result.error}"
            for step in state.steps
            if step.result is not None
        )
        parts = [f"Tools:\n{tools}", f"Question: {state.question}"]
        if observations:
            parts.insert(1, f"What you have already learned:\n{observations}")
        parts.append(
            f"Steps remaining: {state.remaining_steps()}. "
            f"Tool calls remaining: {state.remaining_tool_calls()}."
        )
        return "\n\n".join(parts)

    def _finish(
        self, state: AgentState, answer: str, *, completed: bool, stop_reason: str
    ) -> AgentOutcome:
        logger.info(
            "agent run finished run_id=%s prompt_version=%s steps=%d tool_calls=%d "
            "tools=%s completed=%s stop_reason=%s duration_ms=%.1f",
            state.run_id,
            AGENT_PROMPT_VERSION,
            state.step_count,
            state.tool_calls,
            ",".join(state.tools_used()) or "-",
            completed,
            stop_reason,
            state.duration_ms,
        )
        return AgentOutcome(
            run_id=str(state.run_id),
            question=state.question,
            answer=answer,
            completed=completed,
            steps=state.transcript(),
            tool_calls=state.tool_calls,
            stop_reason=stop_reason,
        )
