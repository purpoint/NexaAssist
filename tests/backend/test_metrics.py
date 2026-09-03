"""Metrics: counting, timing, and refusing to become a cardinality bomb."""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.config import Settings
from app.jobs.memory import InMemoryJobQueue
from app.jobs.worker import JobWorker
from app.llm.base import LLMPrompt, LLMUsage, StructuredCompletion
from app.main import create_app
from app.observability.factory import METRICS_NAMES, build_metrics, get_metrics
from app.observability.integration import traced_provider, traced_registry
from app.observability.metrics import (
    MAX_SERIES_PER_METRIC,
    InMemoryMetrics,
    Metrics,
    NullMetrics,
)
from app.observability.spans import OMITTED
from app.observability.tracer import NullRecorder, Tracer
from app.routing.router import RouteReason
from app.schemas.intent import IntentAnalysis, IntentCategory
from app.services.assistant import AssistantReply
from app.tools.base import ToolError
from app.tools.execution import ToolExecutor
from app.tools.registry import ToolRegistry

ANALYSIS = IntentAnalysis(intent=IntentCategory.BILLING, confidence=0.9, reason="x")


@pytest.fixture
def metrics() -> InMemoryMetrics:
    return InMemoryMetrics()


# --------------------------------------------------------------------------
# Counting and timing


def test_a_counter_accumulates(metrics: InMemoryMetrics) -> None:
    metrics.increment("requests", {"outcome": "ok"})
    metrics.increment("requests", {"outcome": "ok"}, by=2)
    assert metrics.counter("requests", {"outcome": "ok"}) == 3


def test_label_order_does_not_split_a_series(metrics: InMemoryMetrics) -> None:
    metrics.increment("requests", {"a": "1", "b": "2"})
    metrics.increment("requests", {"b": "2", "a": "1"})
    assert metrics.counter("requests", {"a": "1", "b": "2"}) == 2
    assert metrics.series() == 1


def test_different_labels_are_different_series(metrics: InMemoryMetrics) -> None:
    metrics.increment("requests", {"outcome": "ok"})
    metrics.increment("requests", {"outcome": "error"})
    assert metrics.counter("requests", {"outcome": "ok"}) == 1
    assert metrics.total("requests") == 2


def test_an_unrecorded_counter_reads_as_zero(metrics: InMemoryMetrics) -> None:
    assert metrics.counter("never-touched") == 0


def test_durations_are_recorded(metrics: InMemoryMetrics) -> None:
    metrics.observe("duration_ms", 10.0, {"route": "assistant"})
    metrics.observe("duration_ms", 30.0, {"route": "assistant"})
    assert metrics.count("duration_ms", {"route": "assistant"}) == 2
    assert sum(metrics.observations("duration_ms", {"route": "assistant"})) == 40.0


def test_a_snapshot_renders_labels_readably(metrics: InMemoryMetrics) -> None:
    metrics.increment("requests", {"outcome": "ok"})
    metrics.observe("duration_ms", 5.0)
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["requests{outcome=ok}"] == 1
    assert snapshot["durations"]["duration_ms"]["count"] == 1


# --------------------------------------------------------------------------
# Labels are bounded and content-free


def test_prose_never_becomes_a_label(metrics: InMemoryMetrics) -> None:
    """One customer message in a label is one new series per request."""
    metrics.increment("requests", {"question": "where is my money?"})
    assert metrics.counter("requests", {"question": OMITTED}) == 1
    assert "where is my money" not in str(metrics.snapshot())


def test_two_different_messages_share_one_series(metrics: InMemoryMetrics) -> None:
    metrics.increment("requests", {"q": "why is it broken?"})
    metrics.increment("requests", {"q": "where is my refund?"})
    assert metrics.series() == 1


def test_cardinality_is_capped(caplog: pytest.LogCaptureFixture) -> None:
    """Past the cap the series is dropped, not the process."""
    made = InMemoryMetrics(max_series=3)
    with caplog.at_level(logging.WARNING, logger="app.observability.metrics"):
        for n in range(10):
            made.increment("requests", {"id": f"v{n}"})
    assert made.series() == 3
    assert "cardinality capped" in caplog.text


def test_the_cap_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    """A cardinality explosion must not also be a log explosion."""
    made = InMemoryMetrics(max_series=1)
    with caplog.at_level(logging.WARNING, logger="app.observability.metrics"):
        for n in range(20):
            made.increment("requests", {"id": f"v{n}"})
    assert caplog.text.count("cardinality capped") == 1


def test_an_existing_series_still_counts_after_the_cap() -> None:
    made = InMemoryMetrics(max_series=1)
    made.increment("requests", {"id": "a"})
    made.increment("requests", {"id": "b"})
    made.increment("requests", {"id": "a"})
    assert made.counter("requests", {"id": "a"}) == 2


def test_the_default_cap_is_sane() -> None:
    assert 10 < MAX_SERIES_PER_METRIC <= 1000


# --------------------------------------------------------------------------
# Wiring


def test_both_recorders_satisfy_the_protocol() -> None:
    assert isinstance(NullMetrics(), Metrics)
    assert isinstance(InMemoryMetrics(), Metrics)


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["metrics_recorder"].annotation
    assert set(METRICS_NAMES) == set(allowed.__args__)


def test_the_null_recorder_records_nothing() -> None:
    made = NullMetrics()
    made.increment("requests")
    made.observe("duration_ms", 1.0)  # must not raise


def test_the_configured_recorder_is_built() -> None:
    assert isinstance(build_metrics(Settings(metrics_recorder="none")), NullMetrics)
    assert isinstance(build_metrics(Settings()), InMemoryMetrics)


def test_the_recorder_is_a_single_shared_instance() -> None:
    """A fresh one per request would report every counter as one."""
    assert get_metrics() is get_metrics()


# --------------------------------------------------------------------------
# The seams actually record


class Params(BaseModel):
    value: int = 0


class Adder:
    name = "adder"
    description = "Adds one."
    parameters = Params

    async def run(self, params: BaseModel) -> int:
        return params.value + 1


class Breaking:
    name = "breaking"
    description = "Fails."
    parameters = Params

    async def run(self, params: BaseModel) -> int:
        raise ToolError("no")


class Provider:
    name = "fake"

    async def complete_structured(self, *, prompt, schema, config=None):
        return StructuredCompletion[schema](
            output=ANALYSIS,
            provider=self.name,
            model="test-model",
            usage=LLMUsage(input_tokens=10, output_tokens=5),
        )


@pytest.mark.anyio
async def test_tool_runs_are_counted_and_timed(metrics: InMemoryMetrics) -> None:
    tracer = Tracer(NullRecorder())
    registry = ToolRegistry()
    registry.register(Adder())
    executor = ToolExecutor(traced_registry(registry, tracer, metrics))

    await executor.execute("adder", {"value": 1})
    assert metrics.counter("tool_runs_total", {"tool": "adder", "outcome": "ok"}) == 1
    assert metrics.count("tool_duration_ms", {"tool": "adder"}) == 1


@pytest.mark.anyio
async def test_a_failing_tool_is_counted_as_an_error(metrics: InMemoryMetrics) -> None:
    tracer = Tracer(NullRecorder())
    registry = ToolRegistry()
    registry.register(Breaking())
    executor = ToolExecutor(traced_registry(registry, tracer, metrics))

    await executor.execute("breaking", {"value": 1})
    assert (
        metrics.counter("tool_runs_total", {"tool": "breaking", "outcome": "error"}) == 1
    )


@pytest.mark.anyio
async def test_model_calls_and_tokens_are_counted(metrics: InMemoryMetrics) -> None:
    provider = traced_provider(
        Provider(), Tracer(NullRecorder()), metrics=metrics
    )
    await provider.complete_structured(
        prompt=LLMPrompt(system="s", user="u"), schema=IntentAnalysis
    )
    labels = {"provider": "fake", "model": "test-model"}
    assert metrics.counter("llm_calls_total", labels) == 1
    assert metrics.counter("llm_input_tokens_total", labels) == 10
    assert metrics.counter("llm_output_tokens_total", labels) == 5


@pytest.mark.anyio
async def test_job_outcomes_are_counted(metrics: InMemoryMetrics) -> None:
    from app.jobs.handlers import JobHandlerRegistry

    class Recorder:
        name = "recorder"
        payload = Params

        async def run(self, params: BaseModel) -> None:
            return None

    registry = JobHandlerRegistry()
    registry.register(Recorder())
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry, metrics=metrics)

    await queue.enqueue("recorder", {"value": 1})
    await queue.enqueue("nobody_handles_this")
    await worker.drain()

    assert (
        metrics.counter("job_runs_total", {"job": "recorder", "outcome": "succeeded"})
        == 1
    )
    assert (
        metrics.counter(
            "job_runs_total",
            {"job": "nobody_handles_this", "outcome": "dead_lettered"},
        )
        == 1
    )


@pytest.mark.anyio
async def test_a_worker_without_metrics_still_works() -> None:
    """Additive: every existing caller passes nothing."""
    from app.jobs.handlers import JobHandlerRegistry

    queue = InMemoryJobQueue()
    await queue.enqueue("absent")
    assert await JobWorker(queue, JobHandlerRegistry()).run_once() is not None


# --------------------------------------------------------------------------
# Over HTTP


class StubAssistant:
    async def respond(self, message: str, **kwargs: object) -> AssistantReply:
        return AssistantReply(
            reply="a reply",
            intent=IntentCategory.BILLING,
            confidence=0.9,
            handler="agent",
            route_reason=RouteReason.MATCHED,
            fallback=False,
            handled=True,
        )


def test_assistant_requests_are_counted_with_bounded_labels() -> None:
    from app.api.v1.assistant import get_assistant_service

    recorder = InMemoryMetrics()
    app = create_app()
    app.dependency_overrides[get_assistant_service] = StubAssistant
    app.dependency_overrides[get_metrics] = lambda: recorder

    with TestClient(app) as client:
        for _ in range(3):
            client.post(
                "/api/v1/assistant/messages",
                json={"message": "my card 4242 was charged twice"},
            )

    assert recorder.total("assistant_requests_total") == 3
    assert recorder.count("assistant_duration_ms") == 3
    rendered = str(recorder.snapshot())
    assert "4242" not in rendered and "charged twice" not in rendered
    assert "intent=billing" in rendered and "handler=agent" in rendered
