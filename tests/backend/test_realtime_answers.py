"""Streamed answers over the socket.

Driven by a deterministic streaming provider, so what is under test is the
composition -- frame order, the single-flight rule, failure reporting -- and
not any particular model output.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.api.v1.realtime import get_streaming_provider, reset_registry
from app.llm.base import LLMConfig, LLMPrompt
from app.llm.errors import LLMTimeoutError, LLMUnavailableError
from app.llm.streaming import StaticStreamingProvider, StreamingLLMProvider
from app.main import create_app
from app.schemas.intent import IntentCategory
from app.routing.router import RouteReason
from app.schemas.document import Citation
from app.services.assistant import AssistantReply
from app.core.exceptions import AppError
from app.realtime.answers import (
    BUSY_CODE,
    BUSY_MESSAGE,
    PIPELINE_FAILED_CODE,
    STREAM_FAILED_MESSAGE,
    AnswerStreamer,
    chunk,
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


# --------------------------------------------------------------------------
# The grounded path
#
# The socket used to answer with unsourced prose while the HTTP endpoint ran
# the full pipeline, so the client -- which prefers the socket whenever one is
# open -- never showed a citation. These pin the behaviour that replaced it.


ANSWER = "Standard shipping takes 3 to 5 business days within the country."

CITATION = Citation(
    document_id=uuid.uuid4(),
    document_title="Shipping and delivery",
    ordinal=0,
    excerpt="Standard shipping takes 3 to 5 business days within the country.",
    similarity=0.83,
)


def reply(**overrides: object) -> AssistantReply:
    """A pipeline result, grounded and unescalated unless said otherwise."""
    fields: dict = {
        "reply": ANSWER,
        "intent": IntentCategory.PRODUCT_QUESTION,
        "confidence": 0.98,
        "handler": "knowledge_base",
        "route_reason": RouteReason.MATCHED,
        "fallback": False,
        "handled": True,
        "citations": [CITATION],
    }
    fields.update(overrides)
    return AssistantReply(**fields)


class RecordingRecorder:
    """A turn recorder that only remembers it was asked."""

    def __init__(self) -> None:
        self.recorded: list[tuple[uuid.UUID, object, str]] = []

    def scope_to(self, scope: object) -> None:
        pass

    async def record(self, conversation_id, role, content) -> None:
        self.recorded.append((conversation_id, role, content))


class FakeAnswerer:
    """A pipeline that answers without a database or a model."""

    def __init__(self, result: AssistantReply | Exception, available: bool = True) -> None:
        self._result = result
        self._available = available
        self.calls: list[tuple[str, uuid.UUID | None]] = []
        self.scope = "unset"

    @property
    def available(self) -> bool:
        return self._available

    def scope_to(self, scope: object) -> None:
        self.scope = scope

    async def answer(
        self, question: str, *, conversation_id: uuid.UUID | None = None
    ) -> AssistantReply:
        self.calls.append((question, conversation_id))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def ask(streamer: AnswerStreamer, question: str = "How long is shipping?", **kw):
    socket = FakeSocket()
    await streamer.answer(Connection(id="c1", socket=socket), question, **kw)
    return socket.sent


@pytest.mark.anyio
async def test_the_socket_answers_from_the_pipeline() -> None:
    answerer = FakeAnswerer(reply())
    sent = await ask(AnswerStreamer(StaticStreamingProvider(), None, answerer))

    assert answerer.calls, "the pipeline was never asked"
    complete = sent[-1]
    assert complete["type"] == ServerMessageType.COMPLETE
    assert complete["text"] == ANSWER


@pytest.mark.anyio
async def test_the_deltas_reproduce_the_approved_answer() -> None:
    """The frame contract: joining the deltas gives the text exactly.

    It matters more here than before. These deltas are a rendering of an
    answer policy has already approved, so a client that concatenates them
    must arrive at the approved wording and not an approximation of it.
    """
    sent = await ask(AnswerStreamer(StaticStreamingProvider(), None, FakeAnswerer(reply())))
    deltas = [f["text"] for f in sent if f["type"] == ServerMessageType.DELTA]
    assert deltas
    assert "".join(deltas) == ANSWER


@pytest.mark.anyio
async def test_the_citations_reach_the_client() -> None:
    """The point of the change. Without these the client cannot show a source
    even when the answer has one."""
    sent = await ask(AnswerStreamer(StaticStreamingProvider(), None, FakeAnswerer(reply())))
    complete = sent[-1]
    assert complete["grounded"] is True
    assert [c["document_title"] for c in complete["citations"]] == [
        "Shipping and delivery"
    ]


@pytest.mark.anyio
async def test_an_escalated_answer_says_so() -> None:
    """A billing question is answered by a person. A client that cannot tell
    that apart from a normal answer will present a handoff as a resolution."""
    escalated = reply(handled=False, escalated=True, citations=[], handler="agent")
    sent = await ask(AnswerStreamer(StaticStreamingProvider(), None, FakeAnswerer(escalated)))

    complete = sent[-1]
    assert complete["escalated"] is True
    assert complete["grounded"] is True
    assert complete["citations"] == []


@pytest.mark.anyio
async def test_the_pipeline_is_given_the_conversation() -> None:
    """It records both turns itself, which is why the streamer must not."""
    answerer = FakeAnswerer(reply())
    recorder = RecordingRecorder()
    conversation = uuid.uuid4()

    await ask(
        AnswerStreamer(StaticStreamingProvider(), recorder, answerer),
        conversation_id=conversation,
    )

    assert answerer.calls == [("How long is shipping?", conversation)]
    assert recorder.recorded == [], "the exchange would have been written twice"


@pytest.mark.anyio
async def test_a_pipeline_failure_is_reported_as_its_own_code() -> None:
    """An unknown conversation is the client's mistake, and its code says so
    rather than becoming a generic failure."""
    refusal = AppError("No such conversation.")
    refusal.code = "conversation_not_found"
    sent = await ask(AnswerStreamer(StaticStreamingProvider(), None, FakeAnswerer(refusal)))

    assert sent[-1]["type"] == ServerMessageType.ERROR
    assert sent[-1]["code"] == "conversation_not_found"


@pytest.mark.anyio
async def test_an_unexpected_failure_does_not_leak_its_message() -> None:
    """A pipeline error's text can name a model, a table, or a DSN."""
    boom = RuntimeError("connect to postgres://user:hunter2@db:5432 failed")
    sent = await ask(AnswerStreamer(StaticStreamingProvider(), None, FakeAnswerer(boom)))

    frame = sent[-1]
    assert frame["type"] == ServerMessageType.ERROR
    assert frame["code"] == PIPELINE_FAILED_CODE
    assert "hunter2" not in json.dumps(frame)
    assert "postgres" not in json.dumps(frame)


@pytest.mark.anyio
async def test_without_a_database_it_falls_back_and_admits_it() -> None:
    """Unsourced prose is still an answer. It is not a sourced one, and the
    frame is what tells the client which it got."""
    answerer = FakeAnswerer(reply(), available=False)
    sent = await ask(AnswerStreamer(StaticStreamingProvider(), None, answerer))

    assert answerer.calls == []
    complete = sent[-1]
    assert complete["type"] == ServerMessageType.COMPLETE
    assert complete["grounded"] is False
    assert complete["citations"] == []


@pytest.mark.anyio
async def test_the_pipeline_is_still_single_flight() -> None:
    """One question at a time per connection, as before -- a pipeline run is
    more expensive than a stream, not less."""
    streamer = AnswerStreamer(StaticStreamingProvider(), None, FakeAnswerer(reply()))
    socket = FakeSocket()
    connection = Connection(id="c1", socket=socket)

    async def hold(*_a: object, **_k: object) -> AssistantReply:
        await asyncio.sleep(0.05)
        return reply()

    streamer._answerer.answer = hold  # type: ignore[method-assign]
    first = asyncio.create_task(streamer.answer(connection, "one"))
    await asyncio.sleep(0)
    await streamer.answer(connection, "two")
    await first

    assert any(f.get("code") == BUSY_CODE for f in socket.sent)


# --------------------------------------------------------------------------
# Chunking


@pytest.mark.parametrize(
    "text",
    (
        "short",
        ANSWER,
        "Trailing whitespace matters   ",
        "multiple   internal    spaces",
        "a" * 200,
    ),
)
def test_chunking_reproduces_the_text_exactly(text: str) -> None:
    assert "".join(chunk(text)) == text


def test_chunking_an_empty_answer_yields_nothing() -> None:
    assert list(chunk("")) == []


def test_chunking_produces_more_than_one_piece_for_a_real_answer() -> None:
    """Otherwise the client's incremental rendering has nothing to render."""
    assert len(list(chunk(ANSWER))) > 1
