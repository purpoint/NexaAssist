"""Groq streaming completions.

The second of the two modules permitted to import the SDK, alongside
``groq_provider``. It reuses that module's client construction and error
translation rather than restating either: two copies of a rule about which
exception means "configuration" and which means "try again" is two copies that
will disagree.

The deadline covers the *whole* stream, not the first delta. A stream that
delivers one token and then stalls has failed in a way that bounding only the
initial response would never catch, and a caller waiting on it cannot tell slow
from dead.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import groq

from app.core.logging import get_logger
from app.llm.base import LLMConfig, LLMPrompt
from app.llm.errors import (
    LLMConfigurationError,
    LLMError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.providers.groq_provider import PROVIDER_NAME, build_client

logger = get_logger(__name__)


class GroqStreamingProvider:
    """Streams chat completions from Groq as text deltas."""

    name = PROVIDER_NAME

    def __init__(
        self,
        config: LLMConfig,
        client: groq.AsyncGroq | None = None,
    ) -> None:
        self._config = config
        self._client = client if client is not None else build_client(config)

    async def stream_text(
        self,
        *,
        prompt: LLMPrompt,
        config: LLMConfig | None = None,
    ) -> AsyncIterator[str]:
        """Yield the model's response in order, as text deltas."""
        effective = config or self._config
        request: dict[str, Any] = {
            "model": effective.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "max_completion_tokens": effective.max_output_tokens,
            "stream": True,
        }
        if effective.temperature is not None:
            request["temperature"] = effective.temperature

        try:
            # Wraps the entire consumption of the stream, so a stall partway
            # through is bounded exactly as a stall at the start would be.
            async with asyncio.timeout(effective.total_timeout_seconds):
                stream = await self._client.chat.completions.create(**request)
                async for chunk in stream:
                    delta = _delta_of(chunk)
                    if delta:
                        yield delta

        except TimeoutError as exc:
            raise self._fail(LLMTimeoutError, effective, detail="scope=total") from exc
        except groq.APITimeoutError as exc:
            raise self._fail(LLMTimeoutError, effective, detail="scope=attempt") from exc
        except groq.RateLimitError as exc:
            # Streaming has no partial-retry story: a caller that has already
            # rendered half an answer cannot start it again. Reported, not slept
            # through.
            raise self._fail(LLMError, effective, exc=exc) from exc
        except (
            groq.AuthenticationError,
            groq.PermissionDeniedError,
            groq.NotFoundError,
        ) as exc:
            raise self._fail(LLMConfigurationError, effective, exc=exc) from exc
        except (groq.BadRequestError, groq.UnprocessableEntityError) as exc:
            raise self._fail(LLMRequestError, effective, exc=exc) from exc
        except groq.APIStatusError as exc:
            failure = LLMUnavailableError if exc.status_code >= 500 else LLMError
            raise self._fail(failure, effective, exc=exc) from exc
        except groq.APIConnectionError as exc:
            raise self._fail(LLMUnavailableError, effective, exc=exc) from exc
        except groq.APIError as exc:
            raise self._fail(LLMError, effective, exc=exc) from exc

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
            "llm stream failed provider=%s model=%s error_category=%s%s%s",
            self.name,
            config.model,
            failure.code,
            f" status={exc.status_code}" if isinstance(exc, groq.APIStatusError) else "",
            f" {detail}" if detail else "",
        )
        return failure(details={"provider": self.name, "model": config.model})


def _delta_of(chunk: object) -> str | None:
    """Read the text delta out of one streamed chunk.

    Defensive by necessity: the shape is the SDK's, a chunk carrying only a
    finish reason has no content, and a stream must not end because one frame
    was shaped unexpectedly.
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None)
    return content if isinstance(content, str) and content else None
