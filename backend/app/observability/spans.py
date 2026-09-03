"""What one unit of traced work looks like.

A span records *that* something ran, how long it took, and how it ended. It
never records what was said. Every other layer in this codebase already refuses
to log message bodies, prompts, and model output; a trace that quietly carried
them would undo all of it in one place, and traces are the thing most likely to
be shipped to somewhere outside the service.

So attribute values are identifiers, counts, categories, and flags -- nothing
else -- and that is enforced rather than asked for. A value is kept only if it
could not be prose: numbers and booleans always, and a string only when it is
short *and* contains no whitespace.

The whitespace rule is the load-bearing half. A length cap alone cannot tell a
short question from a short identifier -- "how do I get my money back?" is well
under any sensible limit -- while every attribute this system actually records
is a single token: a hex id, a model name, an intent category, an error code,
an outcome. Anything with a space in it is a sentence, and a sentence in a
trace is content.

Sanitising never raises. Tracing is not allowed to break the work it observes:
a span that throws while describing a successful request turns an observability
mistake into an outage.
"""

import uuid
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

MAX_ATTRIBUTE_LENGTH = 120
"""Longest string an attribute may hold.

Comfortably above any identifier, model name, or category this system uses, and
far below anything that could carry a message, a document, or a reply. The
second half of the rule -- no whitespace -- is what actually excludes prose.
"""

OMITTED = "<omitted>"
"""Stands in for a value that was not safe to record."""


class SpanKind(StrEnum):
    """Which layer produced the span.

    Fixed rather than free-form: a trace is only queryable if the same kind of
    work is named the same way every time.
    """

    REQUEST = "request"
    AGENT = "agent"
    TOOL = "tool"
    WORKFLOW = "workflow"
    LLM = "llm"
    ROUTING = "routing"
    RETRIEVAL = "retrieval"


class SpanStatus(StrEnum):
    """How the work ended."""

    OK = "ok"
    ERROR = "error"


class Span(BaseModel):
    """One completed unit of traced work."""

    model_config = ConfigDict(frozen=True)

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind
    status: SpanStatus = SpanStatus.OK
    duration_ms: float = 0.0
    error_category: str | None = Field(
        default=None,
        description=(
            "The error's type or application code -- never its message, which "
            "can quote a prompt, a row, or a connection string."
        ),
    )
    attributes: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is SpanStatus.OK

    @property
    def root(self) -> bool:
        return self.parent_span_id is None


def new_id() -> str:
    """An opaque identifier. Hex, so it is safe in a log line or a header."""
    return uuid.uuid4().hex[:16]


def sanitise_attributes(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reduce attributes to values that cannot carry content.

    Scalars pass through; a long string, a mapping, or a sequence is replaced
    with :data:`OMITTED`. Replaced rather than dropped so a reader can tell the
    difference between "this attribute was never set" and "this attribute held
    something it should not have".
    """
    if not raw:
        return {}
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        clean[str(key)] = _safe_value(value)
    return clean


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > MAX_ATTRIBUTE_LENGTH or any(c.isspace() for c in value):
            return OMITTED
        return value
    # A dict, a list, a model, a document -- anything with structure is the
    # shape content arrives in.
    return OMITTED
