"""Tracing and accounting over the real execution paths.

Every wrapper here satisfies the same protocol as the thing it wraps, so these
tests drive the genuine agent loop, tool executor, and registry rather than
stand-ins for them.
"""

import logging
from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.agent.loop import AgentLoop
from app.agent.state import AgentBudget
from app.llm.base import LLMConfig, LLMPrompt, LLMProvider, LLMUsage, StructuredCompletion
from app.llm.errors import LLMTimeoutError
from app.observability.cost import ModelPricing, PricingTable, UsageLedger
from app.observability.integration import (
    TracedIntentHandler,
    TracedLLMProvider,
    TracedTool,
    result_attributes,
    traced_provider,
    traced_registry,
)
from app.observability.spans import SpanKind, SpanStatus
from app.observability.tracer import InMemoryRecorder, Tracer
from app.routing.handlers import HandlerRequest, HandlerResponse, IntentHandler
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.tools.base import Tool, ToolError
from app.tools.execution import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.results import ToolOutcome

pytestmark = pytest.mark.anyio

MODEL = "test-model"


@pytest.fixture
def recorder() -> InMemoryRecorder:
    return InMemoryRecorder()


@pytest.fixture
def tracer(recorder: InMemoryRecorder) -> Tracer:
    return Tracer(recorder)


@pytest.fixture
def pricing() -> PricingTable:
    return PricingTable([
        ModelPricing(
            model=MODEL,
            input_per_million=Decimal("1.00"),
            output_per_million=Decimal("2.00"),
        )
    ])


class Params(BaseModel):
    value: int = 0


class Adder:
    name = "adder"
    description = "Adds one to the value."
    parameters = Params

    async def run(self, params: BaseModel) -> int:
        return params.value + 1


class Breaking:
    name = "breaking"
    description = "Always fails."
    parameters = Params

    async def run(self, params: BaseModel) -> int:
        raise ToolError("cannot do that")


class Provider:
    """A provider returning a configured completion, with configurable usage."""

    name = "fake"

    def __init__(
        self,
        output: BaseModel,
        *,
        usage: LLMUsage | None = None,
        error: Exception | None = None,
    ) -> None:
        self._output = output
        self._usage = usage if usage is not None else LLMUsage()
        self._error = error
        self.calls = 0

    async def complete_structured(
        self, *, prompt: LLMPrompt, schema: type[BaseModel], config: LLMConfig | None = None
    ) -> StructuredCompletion:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return StructuredCompletion[schema](
            output=self._output,
            provider=self.name,
            model=MODEL,
            stop_reason="end_turn",
            usage=self._usage,
        )


class Echoing:
    name = "echoing"

    def __init__(self, handled: bool = True) -> None:
        self._handled = handled

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        return HandlerResponse(
            handler=self.name, reply="a reply nobody should log", handled=self._handled
        )


ANALYSIS = IntentAnalysis(
    intent=IntentCategory.BILLING, confidence=0.9, reason="fixture"
)


# --------------------------------------------------------------------------
# The wrappers satisfy the protocols they wrap


def test_wrappers_satisfy_their_protocols(tracer: Tracer) -> None:
    assert isinstance(TracedTool(Adder(), tracer), Tool)
    assert isinstance(TracedLLMProvider(Provider(ANALYSIS), tracer), LLMProvider)
    assert isinstance(TracedIntentHandler(Echoing(), tracer), IntentHandler)


def test_a_traced_tool_keeps_its_identity(tracer: Tracer) -> None:
    """A wrapper that renamed its subject would break the registry and schema."""
    traced = TracedTool(Adder(), tracer)
    assert traced.name == "adder"
    assert traced.description == Adder.description
    assert traced.parameters is Params


def test_a_traced_provider_keeps_the_wrapped_name(tracer: Tracer) -> None:
    assert TracedLLMProvider(Provider(ANALYSIS), tracer).name == "fake"


def test_a_traced_registry_is_a_new_registry(tracer: Tracer) -> None:
    original = ToolRegistry()
    original.register(Adder())
    traced = traced_registry(original, tracer)
    assert traced is not original
    assert traced.names() == original.names() == ["adder"]


# --------------------------------------------------------------------------
# Correlation across the real execution path


async def test_an_agent_run_correlates_every_span(
    tracer: Tracer, recorder: InMemoryRecorder, pricing: PricingTable
) -> None:
    """One agent run, real loop, real executor: one trace across all of it."""
    from app.agent.loop import AgentDecision

    decision = AgentDecision(action="answer", answer="done", tool=None, tool_input=None)
    provider = traced_provider(Provider(decision), tracer, pricing=pricing)

    registry = ToolRegistry()
    registry.register(Adder())
    traced = traced_registry(registry, tracer)

    agent = AgentLoop(
        traced, ToolExecutor(traced), provider, budget=AgentBudget(max_steps=2)
    )

    with tracer.span("agent.run", SpanKind.AGENT) as root:
        outcome = await agent.run("why?")
        root.set_attribute("tool_calls", outcome.tool_calls)

    trace_ids = {span.trace_id for span in recorder.spans}
    assert len(trace_ids) == 1, "every span in one run shares a trace id"

    by_name = {span.name: span for span in recorder.spans}
    assert by_name["llm.complete_structured"].parent_span_id == by_name["agent.run"].span_id
    assert by_name["agent.run"].parent_span_id is None


async def test_tool_spans_nest_under_the_agent_span(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    registry = ToolRegistry()
    registry.register(Adder())
    traced = traced_registry(registry, tracer)
    executor = ToolExecutor(traced)

    with tracer.span("agent.run", SpanKind.AGENT):
        result = await executor.execute("adder", {"value": 1})

    assert result.outcome is ToolOutcome.OK and result.output == 2
    tool_span = recorder.by_name("tool.run")[0]
    agent_span = recorder.by_name("agent.run")[0]
    assert tool_span.parent_span_id == agent_span.span_id
    assert tool_span.attributes["tool"] == "adder"
    assert tool_span.kind is SpanKind.TOOL


async def test_a_workflow_run_correlates_its_tool_spans(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    """The workflow runner drives the same executor, so it inherits tracing."""
    from app.workflows.definition import Workflow, WorkflowStep, reference_to
    from app.workflows.execution import WorkflowRunner

    registry = ToolRegistry()
    registry.register(Adder())
    traced = traced_registry(registry, tracer)

    workflow = Workflow(
        name="add_twice",
        description="Adds one, twice.",
        inputs=["value"],
        steps=[
            WorkflowStep(id="first", tool="adder", params={"value": reference_to("value")}),
            WorkflowStep(id="second", tool="adder", params={"value": reference_to("first")}),
        ],
    )

    with tracer.span("workflow.run", SpanKind.WORKFLOW, workflow=workflow.name):
        run, outputs = await WorkflowRunner(ToolExecutor(traced)).run(
            workflow, inputs={"value": 1}
        )

    assert run.completed
    assert outputs["second"] == 3
    workflow_span = recorder.by_name("workflow.run")[0]
    tool_spans = recorder.by_name("tool.run")
    assert len(tool_spans) == 2
    assert all(s.parent_span_id == workflow_span.span_id for s in tool_spans)
    assert all(s.trace_id == workflow_span.trace_id for s in tool_spans)


async def test_a_routing_span_wraps_the_handler(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    handler = TracedIntentHandler(Echoing(), tracer)
    response = await handler.handle(
        HandlerRequest(message="I was charged twice", analysis=ANALYSIS)
    )
    span = recorder.by_name("routing.handle")[0]
    assert response.handled is True
    assert span.kind is SpanKind.ROUTING
    assert span.attributes == {"handler": "echoing", "handled": True}


# --------------------------------------------------------------------------
# Accounting


async def test_tokens_and_cost_are_recorded_on_the_llm_span(
    tracer: Tracer, recorder: InMemoryRecorder, pricing: PricingTable
) -> None:
    provider = traced_provider(
        Provider(ANALYSIS, usage=LLMUsage(input_tokens=1_000_000, output_tokens=500_000)),
        tracer,
        pricing=pricing,
    )
    await provider.complete_structured(
        prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
    )
    attrs = recorder.by_name("llm.complete_structured")[0].attributes
    assert attrs["input_tokens"] == 1_000_000
    assert attrs["output_tokens"] == 500_000
    assert attrs["cost"] == "2.000000"
    assert attrs["priced"] is True
    assert attrs["usage_reported"] is True
    assert attrs["model"] == MODEL


async def test_a_ledger_accumulates_across_calls(
    tracer: Tracer, pricing: PricingTable
) -> None:
    ledger = UsageLedger()
    provider = traced_provider(
        Provider(ANALYSIS, usage=LLMUsage(input_tokens=1_000_000)),
        tracer,
        pricing=pricing,
        ledger=ledger,
    )
    for _ in range(3):
        await provider.complete_structured(
            prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
        )
    assert ledger.calls == 3
    assert ledger.input_tokens == 3_000_000
    assert ledger.total_cost == Decimal("3.000000")
    assert ledger.fully_priced is True


async def test_cost_accounting_is_deterministic(
    tracer: Tracer, pricing: PricingTable
) -> None:
    async def run_once() -> Decimal:
        ledger = UsageLedger()
        provider = traced_provider(
            Provider(ANALYSIS, usage=LLMUsage(input_tokens=1234, output_tokens=567)),
            tracer,
            pricing=pricing,
            ledger=ledger,
        )
        await provider.complete_structured(
            prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
        )
        return ledger.total_cost

    assert await run_once() == await run_once()


async def test_missing_usage_does_not_break_execution(
    tracer: Tracer, recorder: InMemoryRecorder, pricing: PricingTable
) -> None:
    """StaticLLMProvider reports LLMUsage(); the call must still succeed."""
    ledger = UsageLedger()
    provider = traced_provider(
        Provider(ANALYSIS), tracer, pricing=pricing, ledger=ledger
    )
    completion = await provider.complete_structured(
        prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
    )
    assert completion.output == ANALYSIS
    attrs = recorder.by_name("llm.complete_structured")[0].attributes
    assert attrs["usage_reported"] is False
    assert ledger.total_cost == Decimal("0")


async def test_an_unpriced_model_does_not_break_execution(tracer: Tracer) -> None:
    ledger = UsageLedger()
    provider = traced_provider(
        Provider(ANALYSIS, usage=LLMUsage(input_tokens=10)), tracer, ledger=ledger
    )
    await provider.complete_structured(
        prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
    )
    assert ledger.input_tokens == 10
    assert ledger.fully_priced is False


# --------------------------------------------------------------------------
# Failure, and previous behaviour


async def test_a_provider_failure_marks_the_span_and_still_raises(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    provider = traced_provider(Provider(ANALYSIS, error=LLMTimeoutError()), tracer)
    with pytest.raises(LLMTimeoutError):
        await provider.complete_structured(
            prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
        )
    span = recorder.by_name("llm.complete_structured")[0]
    assert span.status is SpanStatus.ERROR
    assert span.error_category == "LLMTimeoutError"


async def test_a_failing_tool_still_becomes_a_result_not_an_exception(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    """M6's contract: the executor converts every failure into a ToolResult."""
    registry = ToolRegistry()
    registry.register(Breaking())
    executor = ToolExecutor(traced_registry(registry, tracer))

    result = await executor.execute("breaking", {"value": 1})
    assert result.outcome is ToolOutcome.FAILED
    assert result.error == "cannot do that"
    assert recorder.by_name("tool.run")[0].status is SpanStatus.ERROR


async def test_tracing_does_not_change_tool_results(tracer: Tracer) -> None:
    registry = ToolRegistry()
    registry.register(Adder())
    plain = await ToolExecutor(registry).execute("adder", {"value": 41})
    traced = await ToolExecutor(traced_registry(registry, tracer)).execute(
        "adder", {"value": 41}
    )
    assert plain.output == traced.output == 42
    assert plain.outcome == traced.outcome


async def test_tracing_does_not_change_the_completion(tracer: Tracer) -> None:
    inner = Provider(ANALYSIS, usage=LLMUsage(input_tokens=5))
    wrapped = traced_provider(Provider(ANALYSIS, usage=LLMUsage(input_tokens=5)), tracer)
    direct = await inner.complete_structured(
        prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
    )
    through = await wrapped.complete_structured(
        prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
    )
    assert direct.output == through.output
    assert direct.model == through.model
    assert direct.usage == through.usage


# --------------------------------------------------------------------------
# Content never reaches a span or a log


async def test_no_span_carries_the_question_or_the_reply(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    handler = TracedIntentHandler(Echoing(), tracer)
    provider = traced_provider(Provider(ANALYSIS), tracer)

    with tracer.span("agent.run", SpanKind.AGENT):
        await handler.handle(
            HandlerRequest(message="I was charged twice", analysis=ANALYSIS)
        )
        await provider.complete_structured(
            prompt=LLMPrompt(system="be brief", user="where is my refund?"),
            schema=IntentAnalysis,
        )

    dumped = " ".join(span.model_dump_json() for span in recorder.spans)
    for content in ("charged twice", "nobody should log", "where is my refund", "be brief"):
        assert content not in dumped


async def test_span_logs_carry_no_content(caplog: pytest.LogCaptureFixture) -> None:
    from app.observability.tracer import LoggingRecorder

    tracer = Tracer(LoggingRecorder())
    handler = TracedIntentHandler(Echoing(), tracer)
    with caplog.at_level(logging.INFO, logger="app.observability.tracer"):
        await handler.handle(
            HandlerRequest(message="I was charged twice", analysis=ANALYSIS)
        )
    assert "handler=echoing" in caplog.text
    assert "charged twice" not in caplog.text
    assert "nobody should log" not in caplog.text


def test_result_attributes_exclude_the_output() -> None:
    from app.tools.results import ToolResult

    result = ToolResult.success("adder", {"body": "customer content here"})
    attrs = result_attributes(result)
    assert attrs == {"tool": "adder", "outcome": "ok", "retryable": False}
    assert "customer content here" not in str(attrs)
