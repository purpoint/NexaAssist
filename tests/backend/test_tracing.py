"""Span representation, correlation, and the rule that traces carry no content."""

import asyncio
import logging

import pytest

from app.core.config import Settings
from app.observability.factory import RECORDER_NAMES, build_recorder, build_tracer
from app.observability.spans import (
    MAX_ATTRIBUTE_LENGTH,
    OMITTED,
    Span,
    SpanKind,
    SpanStatus,
    new_id,
    sanitise_attributes,
)
from app.observability.tracer import (
    InMemoryRecorder,
    LoggingRecorder,
    NullRecorder,
    Recorder,
    Tracer,
    current_span,
    current_trace_id,
)


@pytest.fixture
def recorder() -> InMemoryRecorder:
    return InMemoryRecorder()


@pytest.fixture
def tracer(recorder: InMemoryRecorder) -> Tracer:
    return Tracer(recorder)


class Ticking:
    """A clock advancing one second per read, so durations are exact."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


# --------------------------------------------------------------------------
# Attribute safety


@pytest.mark.parametrize(
    "value", [1, 1.5, True, None, "short", "openai/gpt-oss-120b", "llm_timeout", "a1b2c3"]
)
def test_identifier_shaped_values_are_kept(value: object) -> None:
    """Everything this system actually records is a single token."""
    assert sanitise_attributes({"k": value}) == {"k": value}


@pytest.mark.parametrize(
    "value",
    [
        "how do I get my money back?",
        "I was charged twice",
        "Refunds take 5 business days.",
        "a b",
    ],
)
def test_prose_is_replaced_however_short(value: str) -> None:
    """A length cap alone cannot tell a short question from an identifier.

    Every one of these is well under the length limit, and every one is
    content. Whitespace is what separates a sentence from a token.
    """
    assert sanitise_attributes({"k": value}) == {"k": OMITTED}


def test_a_long_string_is_replaced() -> None:
    """The failure being prevented is a prompt arriving in an attribute."""
    assert sanitise_attributes({"k": "x" * (MAX_ATTRIBUTE_LENGTH + 1)}) == {"k": OMITTED}
    assert sanitise_attributes({"k": "x" * MAX_ATTRIBUTE_LENGTH})["k"] != OMITTED


@pytest.mark.parametrize(
    "value", [{"a": 1}, ["a"], ("a",), {"a"}, object(), b"bytes"]
)
def test_structured_values_are_replaced(value: object) -> None:
    """Structure is the shape content arrives in."""
    assert sanitise_attributes({"k": value}) == {"k": OMITTED}


def test_replacement_is_not_deletion() -> None:
    """A reader must tell 'never set' from 'held something unsafe'."""
    cleaned = sanitise_attributes({"k": {"secret": "value"}})
    assert "k" in cleaned and cleaned["k"] == OMITTED


def test_sanitising_never_raises() -> None:
    class Hostile:
        def __repr__(self) -> str:
            raise RuntimeError("nope")

    assert sanitise_attributes({"k": Hostile()}) == {"k": OMITTED}


def test_missing_attributes_are_empty() -> None:
    assert sanitise_attributes(None) == {}


def test_ids_are_unique_and_hex() -> None:
    first, second = new_id(), new_id()
    assert first != second
    assert all(c in "0123456789abcdef" for c in first)


# --------------------------------------------------------------------------
# Spans


def test_a_span_is_frozen() -> None:
    span = Span(trace_id="t", span_id="s", name="n", kind=SpanKind.TOOL)
    with pytest.raises(Exception):
        span.name = "changed"  # type: ignore[misc]


def test_a_span_reports_ok_and_rootness() -> None:
    root = Span(trace_id="t", span_id="s", name="n", kind=SpanKind.AGENT)
    assert root.ok and root.root
    child = Span(
        trace_id="t",
        span_id="s2",
        parent_span_id="s",
        name="n",
        kind=SpanKind.TOOL,
        status=SpanStatus.ERROR,
    )
    assert not child.ok and not child.root


# --------------------------------------------------------------------------
# Correlation


def test_a_root_span_is_recorded(tracer: Tracer, recorder: InMemoryRecorder) -> None:
    with tracer.span("request", SpanKind.REQUEST):
        pass
    span = recorder.spans[0]
    assert span.name == "request" and span.root and span.status is SpanStatus.OK


def test_nested_spans_share_a_trace_and_chain_parents(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with tracer.span("outer", SpanKind.AGENT):
        with tracer.span("middle", SpanKind.TOOL):
            with tracer.span("inner", SpanKind.LLM):
                pass

    inner, middle, outer = recorder.spans
    assert {s.trace_id for s in recorder.spans} == {outer.trace_id}
    assert outer.parent_span_id is None
    assert middle.parent_span_id == outer.span_id
    assert inner.parent_span_id == middle.span_id


def test_children_close_before_their_parent(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    """Recording on exit means the innermost span is emitted first."""
    with tracer.span("outer", SpanKind.AGENT):
        with tracer.span("inner", SpanKind.TOOL):
            pass
    assert [s.name for s in recorder.spans] == ["inner", "outer"]


def test_sibling_spans_share_the_trace_but_not_the_parent_chain(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with tracer.span("outer", SpanKind.AGENT):
        with tracer.span("first", SpanKind.TOOL):
            pass
        with tracer.span("second", SpanKind.TOOL):
            pass
    first, second, outer = recorder.spans
    assert first.parent_span_id == second.parent_span_id == outer.span_id
    assert first.span_id != second.span_id


def test_separate_root_spans_get_separate_traces(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with tracer.span("one", SpanKind.REQUEST):
        pass
    with tracer.span("two", SpanKind.REQUEST):
        pass
    assert recorder.spans[0].trace_id != recorder.spans[1].trace_id


@pytest.mark.anyio
async def test_concurrent_tasks_do_not_adopt_each_others_spans(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    """The reason the parent lives in a ContextVar rather than an attribute."""

    async def run(label: str) -> None:
        with tracer.span(f"{label}-root", SpanKind.REQUEST):
            await asyncio.sleep(0)
            with tracer.span(f"{label}-child", SpanKind.TOOL):
                await asyncio.sleep(0)

    await asyncio.gather(run("a"), run("b"))

    by_name = {span.name: span for span in recorder.spans}
    assert by_name["a-child"].parent_span_id == by_name["a-root"].span_id
    assert by_name["b-child"].parent_span_id == by_name["b-root"].span_id
    assert by_name["a-root"].trace_id != by_name["b-root"].trace_id


def test_the_context_is_restored_after_a_span(tracer: Tracer) -> None:
    assert current_trace_id() is None
    with tracer.span("outer", SpanKind.REQUEST):
        outer_trace = current_trace_id()
        assert outer_trace is not None
        with tracer.span("inner", SpanKind.TOOL):
            assert current_span().name == "inner"
        assert current_span().name == "outer"
    assert current_trace_id() is None


# --------------------------------------------------------------------------
# Failure and duration


def test_an_exception_marks_the_span_and_still_propagates(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with pytest.raises(RuntimeError):
        with tracer.span("failing", SpanKind.TOOL):
            raise RuntimeError("connection to postgresql://user:pw@host lost")

    span = recorder.spans[0]
    assert span.status is SpanStatus.ERROR
    assert span.error_category == "RuntimeError"
    assert "user:pw" not in span.model_dump_json()


def test_a_failing_child_does_not_fail_its_parent(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with tracer.span("outer", SpanKind.AGENT):
        try:
            with tracer.span("inner", SpanKind.TOOL):
                raise ValueError("boom")
        except ValueError:
            pass
    inner, outer = recorder.spans
    assert inner.status is SpanStatus.ERROR
    assert outer.status is SpanStatus.OK


def test_duration_is_measured(recorder: InMemoryRecorder) -> None:
    tracer = Tracer(recorder, clock=Ticking())
    with tracer.span("timed", SpanKind.REQUEST):
        pass
    assert recorder.spans[0].duration_ms == 1000.0


def test_attributes_can_be_added_while_the_span_is_open(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with tracer.span("work", SpanKind.TOOL, planned=1) as span:
        span.set_attribute("outcome", "ok")
        span.set_attributes({"count": 3})
    assert recorder.spans[0].attributes == {"planned": 1, "outcome": "ok", "count": 3}


def test_attributes_added_late_are_sanitised_too(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with tracer.span("work", SpanKind.TOOL) as span:
        span.set_attribute("reply", "x" * 500)
    assert recorder.spans[0].attributes == {"reply": OMITTED}


def test_an_error_can_be_recorded_without_raising(
    tracer: Tracer, recorder: InMemoryRecorder
) -> None:
    with tracer.span("work", SpanKind.TOOL) as span:
        span.record_error("llm_timeout")
    assert recorder.spans[0].status is SpanStatus.ERROR


# --------------------------------------------------------------------------
# Recorders and wiring


def test_a_broken_recorder_does_not_break_the_traced_work() -> None:
    """An observability mistake must not become an outage."""

    class Broken:
        name = "broken"

        def record(self, span: Span) -> None:
            raise RuntimeError("recorder is down")

    tracer = Tracer(Broken())
    with tracer.span("work", SpanKind.TOOL):
        pass  # must not raise


def test_the_logging_recorder_emits_identifiers_not_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracer = Tracer(LoggingRecorder())
    with caplog.at_level(logging.INFO, logger="app.observability.tracer"):
        with tracer.span("answer", SpanKind.LLM, model="test-model") as span:
            span.set_attribute("question", "how do I get my money back?")
    assert "name=answer" in caplog.text
    assert "kind=llm" in caplog.text
    assert "model=test-model" in caplog.text
    assert "money back" not in caplog.text


def test_the_null_recorder_keeps_nothing() -> None:
    tracer = Tracer(NullRecorder())
    with tracer.span("work", SpanKind.TOOL):
        pass


def test_the_memory_recorder_supports_lookup(recorder: InMemoryRecorder) -> None:
    tracer = Tracer(recorder)
    with tracer.span("a", SpanKind.TOOL):
        pass
    with tracer.span("b", SpanKind.TOOL):
        pass
    assert len(recorder) == 2
    assert [s.name for s in recorder.by_name("a")] == ["a"]
    recorder.clear()
    assert len(recorder) == 0


def test_every_recorder_satisfies_the_protocol() -> None:
    for made in (LoggingRecorder(), InMemoryRecorder(), NullRecorder()):
        assert isinstance(made, Recorder)


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["trace_recorder"].annotation
    assert set(RECORDER_NAMES) == set(allowed.__args__)


def test_the_configured_recorder_is_built() -> None:
    assert isinstance(build_recorder(Settings(trace_recorder="memory")), InMemoryRecorder)
    assert isinstance(build_recorder(Settings(trace_recorder="none")), NullRecorder)
    assert isinstance(build_tracer(Settings(trace_recorder="logging")).recorder, LoggingRecorder)


def test_an_unknown_recorder_is_rejected_by_settings() -> None:
    with pytest.raises(Exception):
        Settings(trace_recorder="jaeger")
