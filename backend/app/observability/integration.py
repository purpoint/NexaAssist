"""Putting tracing and accounting onto the existing execution paths.

Composition, not modification. Every seam this milestone needs to observe --
the model provider, a tool, an intent handler -- is already a ``Protocol``, so
a wrapper that satisfies the same protocol slots in at the composition root and
nothing downstream can tell the difference. No M2, M6, M7, M8, or M9 source
file changes, and every existing test keeps testing the same objects.

Spans nest through a ``ContextVar``, so an agent run wrapped in an agent span
automatically parents the tool spans its tools produce, and the LLM spans
underneath those. Nothing has to thread a trace id through a call signature.

Wrapping is opt-in at the root. An unwrapped provider or registry behaves
exactly as it did before, which is what keeps this additive.
"""

from typing import Any, TypeVar

from pydantic import BaseModel

from app.llm.base import LLMConfig, LLMPrompt, LLMProvider, StructuredCompletion
from app.observability.cost import CostEstimate, PricingTable, UsageLedger, estimate_cost
from app.observability.spans import SpanKind
from app.observability.tracer import Tracer
from app.routing.handlers import HandlerRequest, HandlerResponse, IntentHandler
from app.tools.base import Tool
from app.tools.registry import ToolRegistry
from app.tools.results import ToolResult

T = TypeVar("T", bound=BaseModel)


class TracedLLMProvider:
    """An ``LLMProvider`` that records a span, tokens, and cost per call.

    The one place accounting belongs: only the provider knows what a call
    actually consumed, and every caller in the system already goes through this
    protocol to reach it.
    """

    def __init__(
        self,
        provider: LLMProvider,
        tracer: Tracer,
        *,
        pricing: PricingTable | None = None,
        ledger: UsageLedger | None = None,
    ) -> None:
        self._provider = provider
        self._tracer = tracer
        self._pricing = pricing if pricing is not None else PricingTable()
        self._ledger = ledger

    @property
    def name(self) -> str:
        """Reports the wrapped provider's name.

        A wrapper that renamed the thing it wraps would make every log line and
        error detail attribute the call to a provider that does not exist.
        """
        return self._provider.name

    @property
    def ledger(self) -> UsageLedger | None:
        return self._ledger

    async def complete_structured(
        self,
        *,
        prompt: LLMPrompt,
        schema: type[T],
        config: LLMConfig | None = None,
    ) -> StructuredCompletion[T]:
        with self._tracer.span(
            "llm.complete_structured",
            SpanKind.LLM,
            provider=self._provider.name,
            schema=schema.__name__,
        ) as span:
            completion = await self._provider.complete_structured(
                prompt=prompt, schema=schema, config=config
            )
            self._account(span, completion)
            return completion

    def _account(self, span: Any, completion: StructuredCompletion[T]) -> CostEstimate:
        estimate = estimate_cost(
            provider=completion.provider,
            model=completion.model,
            usage=completion.usage,
            pricing=self._pricing,
        )
        span.set_attributes(
            {
                "model": completion.model,
                "input_tokens": estimate.input_tokens,
                "output_tokens": estimate.output_tokens,
                "usage_reported": estimate.usage_reported,
                "priced": estimate.priced,
                # A string, so the recorded value is the exact decimal rather
                # than a float that no longer sums the way the ledger does.
                "cost": str(estimate.total_cost),
                "stop_reason": completion.stop_reason,
            }
        )
        if self._ledger is not None:
            self._ledger.add(estimate)
        return estimate


class TracedTool:
    """A ``Tool`` that records a span around its run.

    Name, description, and parameter model are the wrapped tool's, so the
    registry, the JSON Schema a model sees, and every error message are
    unchanged.
    """

    def __init__(self, tool: Tool, tracer: Tracer) -> None:
        self._tool = tool
        self._tracer = tracer

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def description(self) -> str:
        return self._tool.description

    @property
    def parameters(self) -> type[BaseModel]:
        return self._tool.parameters

    async def run(self, params: BaseModel) -> Any:
        with self._tracer.span("tool.run", SpanKind.TOOL, tool=self._tool.name):
            # Deliberately no result attribute: a tool returns domain objects,
            # and a ticket body is customer content. The executor already
            # records the outcome, and the span records that it ran.
            return await self._tool.run(params)


class TracedIntentHandler:
    """An ``IntentHandler`` that records a span around its work."""

    def __init__(self, handler: IntentHandler, tracer: Tracer) -> None:
        self._handler = handler
        self._tracer = tracer

    @property
    def name(self) -> str:
        return self._handler.name

    async def handle(self, request: HandlerRequest) -> HandlerResponse:
        with self._tracer.span(
            "routing.handle", SpanKind.ROUTING, handler=self._handler.name
        ) as span:
            response = await self._handler.handle(request)
            # Flags and counts only -- never the reply.
            span.set_attribute("handled", response.handled)
            return response


def traced_registry(registry: ToolRegistry, tracer: Tracer) -> ToolRegistry:
    """Return a registry whose tools each record a span.

    A new registry rather than a mutation: the original is often still held by
    a caller, and quietly swapping its contents would make behaviour depend on
    who looked first.
    """
    traced = ToolRegistry()
    for tool in registry:
        traced.register(TracedTool(tool, tracer))
    return traced


def traced_provider(
    provider: LLMProvider,
    tracer: Tracer,
    *,
    pricing: PricingTable | None = None,
    ledger: UsageLedger | None = None,
) -> LLMProvider:
    """Wrap a provider for tracing and accounting."""
    return TracedLLMProvider(provider, tracer, pricing=pricing, ledger=ledger)


def result_attributes(result: ToolResult) -> dict[str, Any]:
    """Span attributes describing a tool result, without its output.

    Offered as a helper so a caller that wants the outcome on a span reaches
    for something that already excludes the payload.
    """
    return {
        "tool": result.tool,
        "outcome": result.outcome.value,
        "retryable": result.retryable,
    }
