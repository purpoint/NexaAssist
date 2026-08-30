"""Groq implementation of :class:`~app.llm.base.LLMProvider`.

The application talks to the protocol; this module is the only place that knows
Groq exists::

    Application -> LLMProvider (protocol) -> GroqProvider -> Groq API

Structured output is assembled by hand because the Groq SDK ships no ``parse``
helper: the request carries a JSON Schema, and the response text is validated
against the caller's Pydantic model here.

Error handling is deliberately coarse: every SDK failure becomes a single
:class:`~app.llm.errors.LLMError`. Classifying timeouts, rate limits, provider
outages, and misconfiguration -- each with its own status code -- is the
remaining M2 step; see ``docs/milestones.md``.

Nothing in this module logs prompts, responses, or credentials.
"""

import time
from typing import Any, TypeVar

import groq
from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.llm.base import LLMConfig, LLMPrompt, LLMUsage, StructuredCompletion
from app.llm.errors import LLMError

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def build_client(config: LLMConfig) -> groq.AsyncGroq:
    """Construct the SDK client.

    When no key is configured the SDK reads ``GROQ_API_KEY`` from the
    environment itself, so a developer who has already exported it needs no
    ``.env`` entry.
    """
    kwargs: dict[str, Any] = {
        # Seconds, and applied per attempt rather than to the whole call.
        "timeout": config.timeout_seconds,
        "max_retries": config.max_retries,
    }
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key.get_secret_value()
    return groq.AsyncGroq(**kwargs)


def strict_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Render a Pydantic model as a schema Groq accepts under ``strict``.

    Constrained decoding requires every object to list all of its properties in
    ``required`` and to set ``additionalProperties: false``. Pydantic emits
    neither by default, so they are added here once rather than hand-written on
    every schema.
    """
    document = schema.model_json_schema()
    _tighten(document)
    return document


def _tighten(node: object) -> None:
    """Apply the strict-mode object rules in place, at every depth."""
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        for value in node.values():
            _tighten(value)
    elif isinstance(node, list):
        for item in node:
            _tighten(item)


class GroqProvider:
    """Calls Groq chat completions with a schema-constrained response."""

    name = "groq"

    def __init__(
        self,
        config: LLMConfig,
        client: groq.AsyncGroq | None = None,
    ) -> None:
        self._config = config
        self._client = client if client is not None else build_client(config)

    async def complete_structured(
        self,
        *,
        prompt: LLMPrompt,
        schema: type[T],
        config: LLMConfig | None = None,
    ) -> StructuredCompletion[T]:
        """Ask the model for an instance of ``schema``."""
        effective = config or self._config
        request: dict[str, Any] = {
            "model": effective.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "max_completion_tokens": effective.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": strict_json_schema(schema),
                },
            },
        }
        # Optional and provider-agnostic: sent only when set deliberately.
        if effective.temperature is not None:
            request["temperature"] = effective.temperature

        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(**request)
        except groq.APIError as exc:
            # Coarse on purpose -- see the module docstring. The cause is
            # chained for the traceback; only non-sensitive fields go into
            # ``details``, which is rendered into the HTTP response body.
            logger.warning(
                "llm request failed provider=%s model=%s error=%s",
                self.name,
                effective.model,
                type(exc).__name__,
            )
            raise self._failure(effective) from exc
        latency_ms = (time.perf_counter() - started) * 1000.0

        content = _message_content(response)
        if content is None:
            logger.warning(
                "llm returned no content provider=%s model=%s finish_reason=%s",
                self.name,
                effective.model,
                _finish_reason(response),
            )
            raise self._failure(effective)

        try:
            output = schema.model_validate_json(content)
        except ValidationError as exc:
            # The failing field paths are safe to log; the payload is not.
            logger.warning(
                "llm output failed schema validation provider=%s model=%s fields=%s",
                self.name,
                effective.model,
                [".".join(str(part) for part in err["loc"]) for err in exc.errors()],
            )
            raise self._failure(effective) from exc

        return StructuredCompletion[schema](
            output=output,
            provider=self.name,
            model=getattr(response, "model", None) or effective.model,
            stop_reason=_finish_reason(response),
            usage=_usage_of(response),
            latency_ms=latency_ms,
        )

    def _failure(self, config: LLMConfig) -> LLMError:
        return LLMError(details={"provider": self.name, "model": config.model})


def _message_content(response: object) -> str | None:
    """Pull the assistant text off a completion, tolerating an empty one."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _finish_reason(response: object) -> str | None:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    reason = getattr(choices[0], "finish_reason", None)
    return reason if isinstance(reason, str) else None


def _usage_of(response: object) -> LLMUsage:
    """Read token counts off a response, tolerating their absence.

    Groq reports OpenAI-style ``prompt_tokens`` / ``completion_tokens``.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return LLMUsage()
    return LLMUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )
