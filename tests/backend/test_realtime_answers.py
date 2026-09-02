"""Streamed answers over the socket.

Driven by a deterministic streaming provider, so what is under test is the
composition -- frame order, the single-flight rule, failure reporting -- and
not any particular model output.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.api.v1.realtime import get_streaming_provider, reset_registry
from app.llm.base import LLMConfig, LLMPrompt
from app.llm.errors import LLMTimeoutError, LLMUnavailableError
from app.llm.streaming import StaticStreamingProvider, StreamingLLMProvider
from app.main import create_app
from app.realtime.answers import (
    BUSY_CODE,
    BUSY_MESSAGE,
    STREAM_FAILED_MESSAGE,
    AnswerStreamer,
)
from app.realtime.connection import Connection
from app.realtime.envelope import (
    MAX_QUESTION_LENGTH,
    Ask,
    ServerMessageType,
    parse_client_message,
)


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    reset_registry()


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


class FailingProvider:
    """Raises partway through, after some deltas have already been sent."""

    name = "failing"

    def __init__(self, error: Exception, *, after: int = 0) -> None:
        self._error = error
        self._after = after

    async def stream_text(
        self, *, prompt: LLMPrompt, config: LLMConfig | None = None
    ) -> AsyncIterator[str]:
        for index in range(self._after):
            yield f"piece{index} "
        raise self._error


def client_with(provider: StreamingLLMProvider) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_streaming_provider] = lambda: provider
    return TestClient(app)


# --------------------------------------------------------------------------
# The frame


def test_an_ask_frame_parses() -> None:
    assert parse_client_message('{"type": "ask", "question": "why?"}') == Ask(
        type="ask", question="why?"
    )


def test_an_empty_question_is_refused() -> None:
    with pytest.raises(Exception):
        parse_client_message('{"type": "ask", "question": ""}')


def test_an_overlong_question_is_refused() -> None:
    """Bounded separately from the frame: it becomes a model call."""
    payload = json.dumps({"type": "ask", "question": "x" * (MAX_QUESTION_LENGTH + 1)})
    with pytest.raises(Exception):
        parse_client_message(payload)


def test_a_question_at_the_limit_is_accepted() -> None:
    payload = json.dumps({"type": "ask", "question": "x" * MAX_QUESTION_LENGTH})
    assert len(parse_client_message(payload).question) == MAX_QUESTION_LENGTH


# --------------------------------------------------------------------------
# The streamer


@pytest.mark.anyio
async def test_deltas_are_followed_by_a_complete() -> None:
    socket = FakeSocket()
    streamer = AnswerStreamer(StaticStreamingProvider("alpha beta gamma"))
    await streamer.answer(Connection(socket=socket), "why?")

    kinds = [frame["type"] for frame in socket.sent]
    assert kinds == [ServerMessageType.DELTA] * 3 + [ServerMessageType.COMPLETE]


@pytest.mark.anyio
async def test_the_complete_frame_matches_the_deltas() -> None:
    socket = FakeSocket()
    await AnswerStreamer(StaticStreamingProvider("alpha beta gamma")).answer(
        Connection(socket=socket), "why?"
    )
    *deltas, complete = socket.sent
    assert complete["text"] == "".join(frame["text"] for frame in deltas)
    assert complete["text"] == "alpha beta gamma"
    assert complete["deltas"] == len(deltas)


@pytest.mark.anyio
async def test_a_provider_failure_becomes_an_error_frame() -> None:
    socket = FakeSocket()
    await AnswerStreamer(FailingProvider(LLMUnavailableError(), after=2)).answer(
        Connection(socket=socket), "why?"
    )
    assert [frame["type"] for frame in socket.sent] == [
        ServerMessageType.DELTA,
        ServerMessageType.DELTA,
        ServerMessageType.ERROR,
    ]
    assert socket.sent[-1]["message"] == STREAM_FAILED_MESSAGE


@pytest.mark.anyio
async def test_a_failed_stream_sends_no_complete() -> None:
    """A complete frame after a failure would assert a whole answer exists."""
    socket = FakeSocket()
    await AnswerStreamer(FailingProvider(LLMTimeoutError())).answer(
        Connection(socket=socket), "why?"
    )
    assert ServerMessageType.COMPLETE not in {frame["type"] for frame in socket.sent}


@pytest.mark.anyio
async def test_a_failure_never_escapes_to_the_socket_loop() -> None:
    """An escaping error would end the connection over one bad question."""
    await AnswerStreamer(FailingProvider(LLMTimeoutError())).answer(
        Connection(socket=FakeSocket()), "why?"
    )


@pytest.mark.anyio
async def test_the_connection_is_released_after_a_failure() -> None:
    streamer = AnswerStreamer(FailingProvider(LLMTimeoutError()))
    await streamer.answer(Connection(socket=FakeSocket()), "why?")
    assert streamer.busy is False


class GatedProvider:
    """Blocks mid-stream until released, so two answers genuinely overlap."""

    name = "gated"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_text(
        self, *, prompt: LLMPrompt, config: LLMConfig | None = None
    ) -> AsyncIterator[str]:
        yield "first "
        self.started.set()
        await self.release.wait()
        yield "second"


@pytest.mark.anyio
async def test_a_second_question_while_busy_is_refused() -> None:
    """Reached by real overlap, not by setting the flag.

    The rule exists because one socket could otherwise start an unbounded
    number of concurrent model calls -- a cheaper way to exhaust the process
    than opening connections, which at least are counted.
    """
    provider = GatedProvider()
    streamer = AnswerStreamer(provider)
    first_socket, second_socket = FakeSocket(), FakeSocket()

    running = asyncio.create_task(
        streamer.answer(Connection(socket=first_socket), "first question")
    )
    await provider.started.wait()

    await streamer.answer(Connection(socket=second_socket), "second question")
    assert [frame["type"] for frame in second_socket.sent] == [ServerMessageType.ERROR]
    assert second_socket.sent[0]["code"] == BUSY_CODE

    provider.release.set()
    await running
    assert second_socket.sent[0]["message"] == BUSY_MESSAGE
    assert first_socket.sent[-1]["type"] == ServerMessageType.COMPLETE


@pytest.mark.anyio
async def test_questions_run_one_after_another() -> None:
    socket = FakeSocket()
    streamer = AnswerStreamer(StaticStreamingProvider("a b"))
    connection = Connection(socket=socket)
    await streamer.answer(connection, "first")
    await streamer.answer(connection, "second")
    completes = [f for f in socket.sent if f["type"] == ServerMessageType.COMPLETE]
    assert len(completes) == 2


@pytest.mark.anyio
async def test_logs_record_counts_never_the_exchange(
    caplog: pytest.LogCaptureFixture,
) -> None:
    socket = FakeSocket()
    streamer = AnswerStreamer(StaticStreamingProvider("the refund is approved"))
    with caplog.at_level(logging.INFO, logger="app.realtime.answers"):
        await streamer.answer(Connection(socket=socket), "will I get my money back?")

    assert "deltas=4" in caplog.text
    assert "realtime-reply/v1" in caplog.text
    assert "refund" not in caplog.text
    assert "money back" not in caplog.text


# --------------------------------------------------------------------------
# End to end over the socket


def test_asking_over_the_socket_streams_an_answer() -> None:
    with client_with(StaticStreamingProvider("alpha beta")) as client:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.receive_json()
            socket.send_text('{"type": "ask", "question": "why?"}')

            first = socket.receive_json()
            second = socket.receive_json()
            complete = socket.receive_json()

    assert first == {"type": "delta", "text": "alpha "}
    assert second == {"type": "delta", "text": "beta"}
    assert complete["type"] == "complete"
    assert complete["text"] == "alpha beta"


def test_the_socket_still_answers_a_ping_after_a_stream() -> None:
    with client_with(StaticStreamingProvider("one")) as client:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.receive_json()
            socket.send_text('{"type": "ask", "question": "why?"}')
            socket.receive_json()
            socket.receive_json()

            socket.send_text('{"type": "ping"}')
            assert socket.receive_json()["type"] == ServerMessageType.PONG


def test_a_provider_failure_leaves_the_socket_usable() -> None:
    with client_with(FailingProvider(LLMUnavailableError())) as client:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.receive_json()
            socket.send_text('{"type": "ask", "question": "why?"}')
            assert socket.receive_json()["type"] == ServerMessageType.ERROR

            socket.send_text('{"type": "ping"}')
            assert socket.receive_json()["type"] == ServerMessageType.PONG


def test_a_malformed_ask_is_a_protocol_error() -> None:
    with client_with(StaticStreamingProvider()) as client:
        with client.websocket_connect("/api/v1/ws") as socket:
            socket.receive_json()
            socket.send_text('{"type": "ask"}')
            error = socket.receive_json()
    assert error["code"] == "realtime_protocol_error"


def test_the_websocket_route_remains_absent_from_openapi() -> None:
    with client_with(StaticStreamingProvider()) as client:
        assert "/api/v1/ws" not in client.get("/openapi.json").json()["paths"]
