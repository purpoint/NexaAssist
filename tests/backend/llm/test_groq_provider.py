"""Groq provider: failure translation, deadline, and output validation.

Every test drives a stub client. Nothing here touches the network, and no real
credential is used or needed.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import groq
import httpx
import pytest
from pydantic import BaseModel, SecretStr

from app.llm.base import LLMConfig, LLMPrompt
from app.llm.errors import (
    LLMConfigurationError,
    LLMError,
    LLMInvalidOutputError,
    LLMRateLimitError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.providers.groq_provider import GroqProvider, build_client

PROMPT = LLMPrompt(system="classify", user="hello")

# A phrase no client should ever see. Every SDK error below carries it.
SDK_LEAK = "SDK_INTERNAL_DETAIL_a1b2c3"


class Sample(BaseModel):
    answer: str


def config(**overrides: Any) -> LLMConfig:
    base: dict[str, Any] = {
        "provider": "groq",
        "model": "test-model",
        "api_key": SecretStr("gsk-not-a-real-key"),
    }
    return LLMConfig(**{**base, **overrides})


def http_response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return httpx.Response(status, headers=headers or {}, request=request)


def http_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def completion(content: str, finish: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)],
        model="test-model",
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )


class StubClient:
    """Minimal stand-in for ``groq.AsyncGroq``."""

    def __init__(
        self,
        result: Any = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._result = result
        self._error = error
        self._delay = delay
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._delay:
            # Stands in for the SDK sleeping between retries.
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result


def provider(**stub_kwargs: Any) -> tuple[GroqProvider, StubClient]:
    client = StubClient(**stub_kwargs)
    return GroqProvider(config(), client=client), client


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_valid_output_is_parsed_and_returned() -> None:
    p, _ = provider(result=completion('{"answer": "yes"}'))

    result = await p.complete_structured(prompt=PROMPT, schema=Sample)

    assert result.output == Sample(answer="yes")
    assert result.provider == "groq"
    assert result.stop_reason == "stop"
    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 3


# --------------------------------------------------------------------------
# Exception mapping
# --------------------------------------------------------------------------


MAPPINGS: list[tuple[str, Exception, type[LLMError], int]] = [
    (
        "attempt timeout",
        groq.APITimeoutError(request=http_request()),
        LLMTimeoutError,
        504,
    ),
    (
        "rate limit",
        groq.RateLimitError(SDK_LEAK, response=http_response(429), body=None),
        LLMRateLimitError,
        429,
    ),
    (
        "authentication",
        groq.AuthenticationError(SDK_LEAK, response=http_response(401), body=None),
        LLMConfigurationError,
        500,
    ),
    (
        "permission denied",
        groq.PermissionDeniedError(SDK_LEAK, response=http_response(403), body=None),
        LLMConfigurationError,
        500,
    ),
    (
        "not found (bad model id)",
        groq.NotFoundError(SDK_LEAK, response=http_response(404), body=None),
        LLMConfigurationError,
        500,
    ),
    (
        "bad request",
        groq.BadRequestError(SDK_LEAK, response=http_response(400), body=None),
        LLMRequestError,
        500,
    ),
    (
        "unprocessable entity",
        groq.UnprocessableEntityError(SDK_LEAK, response=http_response(422), body=None),
        LLMRequestError,
        500,
    ),
    (
        "server error",
        groq.InternalServerError(SDK_LEAK, response=http_response(500), body=None),
        LLMUnavailableError,
        503,
    ),
    (
        "service unavailable",
        groq.APIStatusError(SDK_LEAK, response=http_response(503), body=None),
        LLMUnavailableError,
        503,
    ),
    (
        "unmapped 4xx",
        groq.APIStatusError(SDK_LEAK, response=http_response(418), body=None),
        LLMError,
        500,
    ),
    (
        "connection failure",
        groq.APIConnectionError(message=SDK_LEAK, request=http_request()),
        LLMUnavailableError,
        503,
    ),
    (
        "generic sdk failure",
        groq.APIError(SDK_LEAK, request=http_request(), body=None),
        LLMError,
        500,
    ),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("sdk_error", "expected", "status"),
    [(e, x, s) for _, e, x, s in MAPPINGS],
    ids=[name for name, _, _, _ in MAPPINGS],
)
async def test_sdk_failures_map_to_application_errors(
    sdk_error: Exception, expected: type[LLMError], status: int
) -> None:
    p, _ = provider(error=sdk_error)

    with pytest.raises(LLMError) as excinfo:
        await p.complete_structured(prompt=PROMPT, schema=Sample)

    assert type(excinfo.value) is expected
    assert excinfo.value.status_code == status


@pytest.mark.anyio
@pytest.mark.parametrize(
    "sdk_error",
    [e for _, e, _, _ in MAPPINGS],
    ids=[name for name, _, _, _ in MAPPINGS],
)
async def test_no_sdk_text_reaches_the_application_error(sdk_error: Exception) -> None:
    """A client must learn the category, never the provider's wording."""
    p, _ = provider(error=sdk_error)

    with pytest.raises(LLMError) as excinfo:
        await p.complete_structured(prompt=PROMPT, schema=Sample)

    error = excinfo.value
    assert SDK_LEAK not in error.message
    assert SDK_LEAK not in str(error.details)
    assert set(error.details) <= {"provider", "model", "retry_after_seconds"}


# --------------------------------------------------------------------------
# Catch ordering (the SDK hierarchy makes this load-bearing)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_timeout_is_not_swallowed_by_the_connection_clause() -> None:
    """APITimeoutError subclasses APIConnectionError."""
    p, _ = provider(error=groq.APITimeoutError(request=http_request()))

    with pytest.raises(LLMTimeoutError):
        await p.complete_structured(prompt=PROMPT, schema=Sample)


@pytest.mark.anyio
async def test_rate_limit_is_not_swallowed_by_the_status_clause() -> None:
    """RateLimitError subclasses APIStatusError."""
    p, _ = provider(
        error=groq.RateLimitError(SDK_LEAK, response=http_response(429), body=None)
    )

    with pytest.raises(LLMRateLimitError):
        await p.complete_structured(prompt=PROMPT, schema=Sample)


# --------------------------------------------------------------------------
# Retry-After
# --------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("header", "expected"),
    [({"retry-after": "34"}, 34.0), ({"retry-after": "1.5"}, 1.5)],
)
async def test_retry_after_is_extracted(header: dict[str, str], expected: float) -> None:
    p, _ = provider(
        error=groq.RateLimitError(
            SDK_LEAK, response=http_response(429, header), body=None
        )
    )

    with pytest.raises(LLMRateLimitError) as excinfo:
        await p.complete_structured(prompt=PROMPT, schema=Sample)

    assert excinfo.value.retry_after_seconds == expected
    assert excinfo.value.details["retry_after_seconds"] == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [{}, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}],
    ids=["absent", "http-date form"],
)
async def test_missing_or_unparsable_retry_after_is_tolerated(
    headers: dict[str, str],
) -> None:
    p, _ = provider(
        error=groq.RateLimitError(
            SDK_LEAK, response=http_response(429, headers), body=None
        )
    )

    with pytest.raises(LLMRateLimitError) as excinfo:
        await p.complete_structured(prompt=PROMPT, schema=Sample)

    assert excinfo.value.retry_after_seconds is None
    assert excinfo.value.headers is None


def test_retry_after_header_rounds_up() -> None:
    assert LLMRateLimitError(retry_after_seconds=1.2).headers == {"Retry-After": "2"}


# --------------------------------------------------------------------------
# Total deadline (covers retry backoff, not one attempt)
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_total_deadline_fires_while_the_sdk_is_backing_off() -> None:
    """The stub sleeps the way the SDK sleeps between retries."""
    client = StubClient(result=completion('{"answer": "late"}'), delay=1.0)
    p = GroqProvider(config(total_timeout_seconds=0.05), client=client)

    with pytest.raises(LLMTimeoutError):
        await p.complete_structured(prompt=PROMPT, schema=Sample)


@pytest.mark.anyio
async def test_a_call_inside_the_deadline_still_succeeds() -> None:
    client = StubClient(result=completion('{"answer": "ok"}'), delay=0.01)
    p = GroqProvider(config(total_timeout_seconds=5.0), client=client)

    assert (await p.complete_structured(prompt=PROMPT, schema=Sample)).output.answer == "ok"


# --------------------------------------------------------------------------
# Invalid structured output
# --------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    ['{"wrong_field": 1}', "not json at all", '{"answer": 12345, "x":']
)
async def test_output_failing_validation_becomes_an_application_error(body: str) -> None:
    p, _ = provider(result=completion(body))

    with pytest.raises(LLMInvalidOutputError):
        await p.complete_structured(prompt=PROMPT, schema=Sample)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(choices=[], model="m", usage=None),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=""), finish_reason="length")],
            model="m",
            usage=None,
        ),
    ],
    ids=["no choices", "empty content"],
)
async def test_empty_response_becomes_an_application_error(response: Any) -> None:
    p, _ = provider(result=response)

    with pytest.raises(LLMInvalidOutputError):
        await p.complete_structured(prompt=PROMPT, schema=Sample)


# --------------------------------------------------------------------------
# Client construction
# --------------------------------------------------------------------------


def test_missing_api_key_becomes_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK raises at construction; that must not escape as a bare 500."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(LLMConfigurationError) as excinfo:
        build_client(config(api_key=None))

    assert excinfo.value.status_code == 500
    assert excinfo.value.details == {"provider": "groq"}


def test_construction_succeeds_with_a_key() -> None:
    client = build_client(config(timeout_seconds=3.0, max_retries=2))

    assert client.timeout == 3.0
    assert client.max_retries == 2
