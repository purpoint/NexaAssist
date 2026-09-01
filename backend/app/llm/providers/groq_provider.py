"""Groq implementation of :class:`~app.llm.base.LLMProvider`.

The application talks to the protocol; this module is the only place that knows
Groq exists::

    Application -> LLMProvider (protocol) -> GroqProvider -> Groq API

Structured output is assembled by hand because the Groq SDK ships no ``parse``
helper: the request carries a JSON Schema, and the response text is validated
against the caller's Pydantic model here.

Two invariants this module maintains:

* **No provider exception escapes.** Every SDK failure is translated into an
  :class:`~app.llm.errors.LLMError` subclass, and no SDK text reaches a client.
* **No content is logged.** Prompts, model output, and credentials never reach
  a log record -- only metadata about the call.
"""

import asyncio
import time
from typing import Any, TypeVar

import groq
from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.llm.base import LLMConfig, LLMPrompt, LLMUsage, StructuredCompletion
from app.llm.errors import (
    LLMConfigurationError,
    LLMError,
    LLMInvalidOutputError,
    LLMRateLimitError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

PROVIDER_NAME = "groq"


def build_client(config: LLMConfig) -> groq.AsyncGroq:
    """Construct the SDK client.

    When no key is configured the SDK reads ``GROQ_API_KEY`` from the
    environment itself, so a developer who has already exported it needs no
    ``.env`` entry. If nothing resolves, the SDK raises at construction time --
    inside the FastAPI dependency, before ``complete_structured`` is ever
    reached -- so it is translated here rather than escaping as a bare 500.
    """
    kwargs: dict[str, Any] = {
        # Seconds, and applied per attempt rather than to the whole call.
        "timeout": config.timeout_seconds,
        "max_retries": config.max_retries,
    }
    if config.api_key is not None:
        kwargs["api_key"] = config.api_key.get_secret_value()
    try:
        return groq.AsyncGroq(**kwargs)
    except groq.GroqError as exc:
        # Only construction failures can occur here -- no request is in flight,
        # so this cannot swallow an API error.
        logger.warning(
            "llm client construction failed provider=%s error_category=%s",
            PROVIDER_NAME,
            LLMConfigurationError.code,
        )
        raise LLMConfigurationError(details={"provider": PROVIDER_NAME}) from exc


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

    name = PROVIDER_NAME

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
        response = await self._request(request, effective)
        latency_ms = (time.perf_counter() - started) * 1000.0

        content = _message_content(response)
        if content is None:
            raise self._fail(
                LLMInvalidOutputError,
                effective,
                detail=f"finish_reason={_finish_reason(response)}",
            )

        try:
            output = schema.model_validate_json(content)
        except ValidationError as exc:
            # Field paths are safe to log; the payload that produced them is not.
            raise self._fail(
                LLMInvalidOutputError,
                effective,
                detail="fields="
                + ",".join(".".join(str(p) for p in e["loc"]) for e in exc.errors()),
            ) from exc

        return StructuredCompletion[schema](
            output=output,
            provider=self.name,
            model=getattr(response, "model", None) or effective.model,
            stop_reason=_finish_reason(response),
            usage=_usage_of(response),
            latency_ms=latency_ms,
        )

    async def _request(self, request: dict[str, Any], config: LLMConfig) -> Any:
        """Perform the call, translating every provider failure.

        The deadline wraps the whole SDK call, not one HTTP attempt: the SDK
        retries and sleeps for backoff *inside* this await, and a real call was
        observed taking 34.8s after a server-directed ``retry-after``. Bounding
        only a single attempt would not bound the request.

        Clauses run most-specific-first, which the SDK's hierarchy makes
        load-bearing: ``APITimeoutError`` is an ``APIConnectionError``, and
        ``RateLimitError`` and friends are ``APIStatusError``s.
        """
        try:
            async with asyncio.timeout(config.total_timeout_seconds):
                return await self._client.chat.completions.create(**request)

        except TimeoutError as exc:  # the total budget above, backoff included
            raise self._fail(LLMTimeoutError, config, detail="scope=total") from exc
        except groq.APITimeoutError as exc:  # one attempt exceeded its timeout
            raise self._fail(LLMTimeoutError, config, detail="scope=attempt") from exc
        except groq.RateLimitError as exc:
            raise self._rate_limited(config, exc) from exc
        except (
            groq.AuthenticationError,
            groq.PermissionDeniedError,
            groq.NotFoundError,
        ) as exc:
            raise self._fail(LLMConfigurationError, config, exc=exc) from exc
        except (groq.BadRequestError, groq.UnprocessableEntityError) as exc:
            raise self._fail(LLMRequestError, config, exc=exc) from exc
        except groq.APIStatusError as exc:
            failure = LLMUnavailableError if exc.status_code >= 500 else LLMError
            raise self._fail(failure, config, exc=exc) from exc
        except groq.APIConnectionError as exc:
            raise self._fail(LLMUnavailableError, config, exc=exc) from exc
        except groq.APIError as exc:  # incl. APIResponseValidationError
            raise self._fail(LLMError, config, exc=exc) from exc

    def _fail(
        self,
        failure: type[LLMError],
        config: LLMConfig,
        *,
        exc: Exception | None = None,
        detail: str | None = None,
    ) -> LLMError:
        """Build the application error and log the category -- never the cause."""
        logger.warning(
            "llm call failed provider=%s model=%s error_category=%s%s%s",
            self.name,
            config.model,
            failure.code,
            f" status={exc.status_code}" if isinstance(exc, groq.APIStatusError) else "",
            f" {detail}" if detail else "",
        )
        return failure(details={"provider": self.name, "model": config.model})

    def _rate_limited(
        self, config: LLMConfig, exc: groq.RateLimitError
    ) -> LLMRateLimitError:
        retry_after = _retry_after_seconds(exc)
        logger.warning(
            "llm call failed provider=%s model=%s error_category=%s retry_after=%s",
            self.name,
            config.model,
            LLMRateLimitError.code,
            retry_after,
        )
        return LLMRateLimitError(
            details={"provider": self.name, "model": config.model},
            retry_after_seconds=retry_after,
        )


def _retry_after_seconds(exc: groq.APIStatusError) -> float | None:
    """Read ``Retry-After`` as seconds, tolerating its absence or an odd value.

    Only the delta-seconds form is read; the HTTP-date form is treated as
    absent rather than guessed at.
    """
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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
