"""Streaming completions.

A protocol of its own rather than a method added to
:class:`~app.llm.base.LLMProvider`. Not every backend can stream, and every
existing caller wants a whole validated object rather than a sequence of
fragments -- widening the one protocol would have obliged both implementations
and every future one to answer a question most callers never ask.

What a stream yields is plain text deltas, and deliberately nothing more.
Structured output and streaming pull in opposite directions: a schema-validated
object is only valid once it is complete, so a stream of partial JSON would be
a stream of things that are not yet the thing. Callers that need a validated
object use ``complete_structured``; callers that need to show progress use
this, and validate nothing until the stream ends.

Bounded like every other provider call: an implementation must finish or raise
within ``config.total_timeout_seconds``, counting anything it does internally.
A stream that stalls forever is worse than one that fails, because nothing
upstream can tell the difference between slow and dead.
"""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from app.core.config import Settings
from app.llm.base import LLMConfig, LLMPrompt

DEFAULT_STATIC_STREAM_TEXT = (
    "This is a deterministic response from the static streaming provider. "
    "It makes no network call and always produces the same deltas."
)


@runtime_checkable
class StreamingLLMProvider(Protocol):
    """A backend that can emit a response progressively."""

    name: str

    def stream_text(
        self,
        *,
        prompt: LLMPrompt,
        config: LLMConfig | None = None,
    ) -> AsyncIterator[str]:
        """Yield the response in order, as text deltas.

        Concatenating every delta must equal the complete response: a consumer
        that joins them is the definition of correct here, and any implementation
        that overlaps or drops text breaks every caller silently.

        Failures are raised as :class:`~app.llm.errors.LLMError` or a subclass.
        No provider-specific exception may escape.
        """
        ...


class StaticStreamingProvider:
    """Deterministic deltas, no network.

    The offline counterpart, and what the suite runs on. Splits a fixed text on
    word boundaries so a consumer sees several deltas rather than one, which is
    the only property a test of streaming actually needs.

    Whitespace is kept on the preceding delta rather than stripped, so joining
    the deltas reproduces the text exactly. A provider whose chunks lose the
    spaces between them looks fine in a test that checks length and wrong in
    every rendering.
    """

    name = "static"

    def __init__(self, text: str = DEFAULT_STATIC_STREAM_TEXT) -> None:
        self._text = text

    async def stream_text(
        self,
        *,
        prompt: LLMPrompt,
        config: LLMConfig | None = None,
    ) -> AsyncIterator[str]:
        for delta in _split_keeping_whitespace(self._text):
            yield delta


def _split_keeping_whitespace(text: str) -> list[str]:
    """Split into word-sized pieces without losing a character."""
    if not text:
        return []
    pieces: list[str] = []
    current = ""
    for character in text:
        current += character
        if character == " ":
            pieces.append(current)
            current = ""
    if current:
        pieces.append(current)
    return pieces


def build_streaming_provider(settings: Settings) -> StreamingLLMProvider:
    """Construct the streaming provider named by ``settings.llm_provider``.

    Keyed off the existing setting rather than a new one: a deployment that
    talks to Groq should stream from Groq, and a second switch would only make
    it possible to configure a contradiction.

    The Groq module is imported lazily, so selecting the static provider never
    imports the SDK -- the same reason the streaming config is projected by the
    factory that already knows how rather than by a second copy here.
    """
    if settings.llm_provider == StaticStreamingProvider.name:
        return StaticStreamingProvider()

    from app.llm.factory import config_from_settings
    from app.llm.providers.groq_streaming import GroqStreamingProvider

    return GroqStreamingProvider(config_from_settings(settings))
