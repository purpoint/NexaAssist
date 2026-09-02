"""The streaming provider contract.

The static provider is exercised directly; the Groq one is driven with a fake
client, so the translation from SDK failure to application error is tested
without a network call or a key.
"""

import ast
from pathlib import Path
from typing import Any

import groq
import httpx
import pytest

from app.core.config import Settings
from app.llm.base import LLMConfig, LLMPrompt
from app.llm.errors import (
    LLMConfigurationError,
    LLMError,
    LLMRequestError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.llm.providers.groq_streaming import GroqStreamingProvider
from app.llm.streaming import (
    DEFAULT_STATIC_STREAM_TEXT,
    StaticStreamingProvider,
    StreamingLLMProvider,
    build_streaming_provider,
)

pytestmark = pytest.mark.anyio

BACKEND_APP = Path(__file__).resolve().parents[2] / "backend" / "app"
PROMPT = LLMPrompt(system="Be brief.", user="Why is the sky blue?")
CONFIG = LLMConfig(provider="groq", model="test-model", total_timeout_seconds=5.0)


async def collect(provider: StreamingLLMProvider) -> list[str]:
    return [delta async for delta in provider.stream_text(prompt=PROMPT, config=CONFIG)]


# --------------------------------------------------------------------------
# The deterministic provider


async def test_the_static_provider_yields_several_deltas() -> None:
    assert len(await collect(StaticStreamingProvider())) > 1


async def test_joining_the_deltas_reproduces_the_text() -> None:
    """The contract a consumer relies on: concatenation is the response."""
    assert "".join(await collect(StaticStreamingProvider())) == DEFAULT_STATIC_STREAM_TEXT


async def test_whitespace_is_not_lost_between_deltas() -> None:
    provider = StaticStreamingProvider("alpha beta gamma")
    deltas = await collect(provider)
    assert deltas == ["alpha ", "beta ", "gamma"]
    assert "".join(deltas) == "alpha beta gamma"


async def test_it_is_deterministic() -> None:
    provider = StaticStreamingProvider()
    assert await collect(provider) == await collect(provider)


async def test_empty_text_streams_nothing() -> None:
    assert await collect(StaticStreamingProvider("")) == []


def test_the_static_provider_satisfies_the_protocol() -> None:
    assert isinstance(StaticStreamingProvider(), StreamingLLMProvider)


def test_the_groq_provider_satisfies_the_protocol() -> None:
    assert isinstance(GroqStreamingProvider(CONFIG, client=object()), StreamingLLMProvider)


# --------------------------------------------------------------------------
# The Groq provider, without a network


class FakeChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [type("Choice", (), {"delta": type("Delta", (), {"content": content})()})()]


class FakeStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> "FakeStream":
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class FakeCompletions:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.request: dict[str, Any] | None = None

    async def create(self, **request: Any) -> Any:
        self.request = request
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.chat = type("Chat", (), {"completions": FakeCompletions(result)})()


def groq_provider_yielding(*contents: str | None) -> GroqStreamingProvider:
    return GroqStreamingProvider(
        CONFIG, client=FakeClient(FakeStream([FakeChunk(c) for c in contents]))
    )


def groq_provider_raising(exc: Exception) -> GroqStreamingProvider:
    return GroqStreamingProvider(CONFIG, client=FakeClient(exc))


def status_error(
    status: int, kind: type[groq.APIStatusError] = groq.APIStatusError
) -> groq.APIStatusError:
    """Build the exception the SDK would actually raise for this status.

    The status-to-subclass mapping happens inside the SDK when it builds an
    error from a response, so constructing APIStatusError directly would
    produce something it never raises -- and would test a branch that only
    exists in the test.
    """
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return kind(
        "upstream said no",
        response=httpx.Response(status, request=request),
        body=None,
    )


async def test_deltas_are_yielded_in_order() -> None:
    assert await collect(groq_provider_yielding("Because ", "of ", "scattering.")) == [
        "Because ",
        "of ",
        "scattering.",
    ]


async def test_chunks_without_content_are_skipped() -> None:
    """A final chunk carries a finish reason and no text."""
    assert await collect(groq_provider_yielding("Hello", None, "")) == ["Hello"]


async def test_the_request_asks_for_a_stream() -> None:
    provider = groq_provider_yielding("hi")
    await collect(provider)
    assert provider._client.chat.completions.request["stream"] is True


async def test_temperature_is_sent_only_when_set() -> None:
    provider = groq_provider_yielding("hi")
    await collect(provider)
    assert "temperature" not in provider._client.chat.completions.request

    warm = LLMConfig(provider="groq", model="m", temperature=0.5)
    provider = GroqStreamingProvider(
        warm, client=FakeClient(FakeStream([FakeChunk("hi")]))
    )
    assert [d async for d in provider.stream_text(prompt=PROMPT)] == ["hi"]
    assert provider._client.chat.completions.request["temperature"] == 0.5


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (groq.APITimeoutError(request=httpx.Request("POST", "https://x")), LLMTimeoutError),
        (status_error(401, groq.AuthenticationError), LLMConfigurationError),
        (status_error(403, groq.PermissionDeniedError), LLMConfigurationError),
        (status_error(404, groq.NotFoundError), LLMConfigurationError),
        (status_error(400, groq.BadRequestError), LLMRequestError),
        (status_error(422, groq.UnprocessableEntityError), LLMRequestError),
        (status_error(500), LLMUnavailableError),
        (status_error(503), LLMUnavailableError),
        (status_error(418), LLMError),
        (groq.APIConnectionError(request=httpx.Request("POST", "https://x")), LLMUnavailableError),
    ],
)
async def test_provider_failures_become_application_errors(
    raised: Exception, expected: type[LLMError]
) -> None:
    with pytest.raises(expected):
        await collect(groq_provider_raising(raised))


async def test_no_sdk_exception_escapes() -> None:
    """A caller may catch LLMError and be sure it has caught everything."""
    with pytest.raises(LLMError):
        await collect(groq_provider_raising(status_error(500)))


async def test_a_rate_limit_is_reported_not_slept_through() -> None:
    """A half-rendered answer cannot be started again."""
    request = httpx.Request("POST", "https://x")
    limited = groq.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )
    assert isinstance(limited, groq.APIStatusError)
    with pytest.raises(LLMError):
        await collect(groq_provider_raising(limited))


async def test_the_error_body_carries_no_provider_detail() -> None:
    with pytest.raises(LLMError) as caught:
        await collect(groq_provider_raising(status_error(500)))
    rendered = caught.value.to_response().model_dump_json()
    assert "upstream said no" not in rendered
    assert "api.groq.com" not in rendered


# --------------------------------------------------------------------------
# Selection and boundaries


def test_the_static_setting_selects_the_static_provider() -> None:
    built = build_streaming_provider(Settings(llm_provider="static"))
    assert isinstance(built, StaticStreamingProvider)


def test_the_groq_setting_selects_the_groq_provider() -> None:
    built = build_streaming_provider(
        Settings(llm_provider="groq", llm_api_key="test-key-not-real")
    )
    assert isinstance(built, GroqStreamingProvider)


def test_streaming_selection_follows_the_one_provider_setting() -> None:
    """A second switch could only be used to configure a contradiction."""
    allowed = Settings.model_fields["llm_provider"].annotation
    assert set(allowed.__args__) == {"groq", "static"}


def test_only_the_two_groq_modules_import_the_sdk() -> None:
    """Previously a comment in requirements.txt; now enforced.

    The boundary is the reason a second provider is an adapter rather than a
    refactor, and a rule that nothing checks is a rule that drifts.
    """
    offenders = set()
    for path in BACKEND_APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n == "groq" or n.startswith("groq.") for n in names):
                offenders.add(path.name)
    assert sorted(offenders) == ["groq_provider.py", "groq_streaming.py"]
