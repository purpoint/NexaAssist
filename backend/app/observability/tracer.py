"""Producing traces, and correlating them across nested work.

Correlation is the whole point. An agent run calls tools, a tool calls a
service, a service calls a provider -- and unless every one of those carries
the same trace id, the record of a slow or failed request is a pile of
unrelated lines. The parent is tracked in a :class:`~contextvars.ContextVar`,
which is per-task under asyncio, so two requests handled concurrently never
adopt each other's spans.

No framework. The repository's roadmap asks for structured tracing, not a
vendor agent, and the recording surface here is one method -- which is what
makes exporting elsewhere later an adapter rather than a rewrite.

Two rules the implementation exists to keep:

* **Tracing never breaks the work it observes.** A recorder that raises is
  logged and ignored; an attribute that could carry content is replaced. An
  observability mistake must not become an outage.
* **A span always closes.** The context manager records on the way out whether
  the body returned or raised, so a failure is a span with an error status
  rather than a span that never appears.
"""

import contextvars
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.observability.spans import (
    Span,
    SpanKind,
    SpanStatus,
    new_id,
    sanitise_attributes,
)

logger = get_logger(__name__)

_current: contextvars.ContextVar["SpanHandle | None"] = contextvars.ContextVar(
    "nexaassist_current_span", default=None
)


@runtime_checkable
class Recorder(Protocol):
    """Where completed spans go."""

    name: str

    def record(self, span: Span) -> None:
        """Accept one finished span. Must not raise."""
        ...


class InMemoryRecorder:
    """Keeps spans in a list. Deterministic, offline, and what tests assert on."""

    name = "memory"

    def __init__(self) -> None:
        self._spans: list[Span] = []

    def record(self, span: Span) -> None:
        self._spans.append(span)

    @property
    def spans(self) -> Sequence[Span]:
        return tuple(self._spans)

    def by_name(self, name: str) -> Sequence[Span]:
        return tuple(span for span in self._spans if span.name == name)

    def clear(self) -> None:
        self._spans.clear()

    def __len__(self) -> int:
        return len(self._spans)


class LoggingRecorder:
    """Emits one line per span through the standard logging configuration.

    Reuses the existing logger rather than a second output path, so the M2
    redaction filter covers trace lines exactly as it covers everything else.
    """

    name = "logging"

    def record(self, span: Span) -> None:
        logger.info(
            "span trace=%s span=%s parent=%s name=%s kind=%s status=%s "
            "duration_ms=%.1f%s%s",
            span.trace_id,
            span.span_id,
            span.parent_span_id or "-",
            span.name,
            span.kind.value,
            span.status.value,
            span.duration_ms,
            f" error={span.error_category}" if span.error_category else "",
            "".join(f" {k}={v}" for k, v in sorted(span.attributes.items())),
        )


class NullRecorder:
    """Discards everything. Tracing turned off without branching at call sites."""

    name = "none"

    def record(self, span: Span) -> None:
        return None


@dataclass
class SpanHandle:
    """A span in progress.

    Handed to the traced body so it can add what it learns -- a tool's outcome,
    a token count -- as it goes, rather than only what was known up front.
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    attributes: dict[str, Any] = field(default_factory=dict)
    status: SpanStatus = SpanStatus.OK
    error_category: str | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes.update(sanitise_attributes({key: value}))

    def set_attributes(self, values: Mapping[str, Any]) -> None:
        self.attributes.update(sanitise_attributes(values))

    def record_error(self, category: str) -> None:
        """Mark the span failed. Category only -- never a message."""
        self.status = SpanStatus.ERROR
        self.error_category = category

    def to_span(self, duration_ms: float) -> Span:
        return Span(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            kind=self.kind,
            status=self.status,
            duration_ms=duration_ms,
            error_category=self.error_category,
            attributes=dict(self.attributes),
        )


class Tracer:
    """Opens spans and hands finished ones to a recorder."""

    def __init__(
        self,
        recorder: Recorder,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._recorder = recorder
        # Injected so a test can make durations exact instead of asserting
        # that a number is merely non-negative.
        self._clock = clock

    @property
    def recorder(self) -> Recorder:
        return self._recorder

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind,
        **attributes: Any,
    ) -> Iterator[SpanHandle]:
        """Open a span, inheriting the current one as its parent.

        Synchronous on purpose: a context manager that does not await works in
        both sync and async callers, and nothing here needs to yield.
        """
        parent = _current.get()
        handle = SpanHandle(
            trace_id=parent.trace_id if parent else new_id(),
            span_id=new_id(),
            parent_span_id=parent.span_id if parent else None,
            name=name,
            kind=kind,
            attributes=sanitise_attributes(attributes),
        )
        token = _current.set(handle)
        started = self._clock()
        try:
            yield handle
        except Exception as exc:
            # Type only. The message can quote a prompt or a row.
            handle.record_error(type(exc).__name__)
            raise
        finally:
            duration = (self._clock() - started) * 1000.0
            _current.reset(token)
            self._emit(handle.to_span(duration))

    def _emit(self, span: Span) -> None:
        try:
            self._recorder.record(span)
        except Exception as exc:  # pragma: no cover - recorder guard
            # Observability must never take down the request it describes.
            logger.warning("span recorder failed error=%s", type(exc).__name__)


def current_trace_id() -> str | None:
    """The trace id of the innermost open span, if any.

    Lets a layer that is not itself tracing -- an error handler, a log line --
    attach the identifier that ties it to everything else.
    """
    handle = _current.get()
    return handle.trace_id if handle else None


def current_span() -> SpanHandle | None:
    return _current.get()
