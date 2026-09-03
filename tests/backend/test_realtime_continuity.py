"""Conversation continuity on the realtime path, without a database.

The recorder is a protocol, so the transport can be driven with a stub and the
socket's behaviour tested on its own. That the stub's real counterpart actually
writes rows is covered in ``tests/backend/db/test_realtime_conversations.py``.
"""

import json
import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.exceptions import AppError
from app.llm.base import LLMConfig, LLMPrompt
from app.llm.streaming import StaticStreamingProvider
from app.models.conversation import MessageRole
from app.realtime.answers import (
    NO_RECORDER_CODE,
    RECORD_FAILED_CODE,
    AnswerStreamer,
)
from app.realtime.connection import Connection
from app.realtime.conversations import TurnRecorder
from app.realtime.envelope import (
    Ask,
    Complete,
    ServerMessageType,
    parse_client_message,
)
from app.services.errors import ConversationNotFoundError

pytestmark = pytest.mark.anyio


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    @property
    def kinds(self) -> list[str]:
        return [frame["type"] for frame in self.sent]


class Recording:
    """Remembers turns instead of writing them."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.turns: list[tuple[uuid.UUID, MessageRole, str]] = []
        self.scope: object = None

    def scope_to(self, scope: object) -> None:
        self.scope = scope

    async def record(
        self, conversation_id: uuid.UUID, role: MessageRole, content: str
    ) -> None:
        if self._error is not None:
            raise self._error
        self.turns.append((conversation_id, role, content))


class FailingLate:
    """Succeeds on the question and fails on the answer."""

    def __init__(self) -> None:
        self.turns: list[MessageRole] = []

    def scope_to(self, scope: object) -> None:
        return None

    async def record(
        self, conversation_id: uuid.UUID, role: MessageRole, content: str
    ) -> None:
        self.turns.append(role)
        if role is MessageRole.ASSISTANT:
            raise RuntimeError("write failed")


class Counting:
    """A provider that reports whether it was ever asked to stream."""

    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_text(
        self, *, prompt: LLMPrompt, config: LLMConfig | None = None
    ) -> AsyncIterator[str]:
        self.calls += 1
        yield "answer"


def streamer(recorder: object | None = None, text: str = "one two") -> AnswerStreamer:
    return AnswerStreamer(StaticStreamingProvider(text), recorder)


# --------------------------------------------------------------------------
# The frame


def test_an_ask_may_carry_a_conversation() -> None:
    parsed = parse_client_message(
        json.dumps({"type": "ask", "question": "why?", "conversation_id": str(uuid.uuid4())})
    )
    assert isinstance(parsed, Ask)
    assert parsed.conversation_id is not None


def test_the_conversation_is_optional() -> None:
    """An existing client that never sends one keeps working."""
    assert parse_client_message('{"type": "ask", "question": "why?"}').conversation_id is None


def test_a_malformed_conversation_id_is_refused() -> None:
    with pytest.raises(Exception):
        parse_client_message('{"type": "ask", "question": "why?", "conversation_id": "nope"}')


def test_the_complete_frame_defaults_to_no_conversation() -> None:
    assert Complete(text="a", deltas=1).conversation_id is None


# --------------------------------------------------------------------------
# Recording


async def test_both_turns_are_recorded_around_the_stream() -> None:
    recorder, socket = Recording(), FakeSocket()
    conversation = uuid.uuid4()
    await streamer(recorder).answer(
        Connection(socket=socket), "why?", conversation_id=conversation
    )

    assert [role for _, role, _ in recorder.turns] == [
        MessageRole.CUSTOMER,
        MessageRole.ASSISTANT,
    ]
    assert recorder.turns[0][2] == "why?"
    assert recorder.turns[1][2] == "one two"
    assert all(cid == conversation for cid, _, _ in recorder.turns)


async def test_the_complete_frame_echoes_the_conversation() -> None:
    socket = FakeSocket()
    conversation = uuid.uuid4()
    await streamer(Recording()).answer(
        Connection(socket=socket), "why?", conversation_id=conversation
    )
    assert socket.sent[-1]["conversation_id"] == str(conversation)


async def test_nothing_is_recorded_without_a_conversation() -> None:
    recorder, socket = Recording(), FakeSocket()
    await streamer(recorder).answer(Connection(socket=socket), "why?")
    assert recorder.turns == []
    assert socket.kinds[-1] == ServerMessageType.COMPLETE
    assert socket.sent[-1]["conversation_id"] is None


# --------------------------------------------------------------------------
# Failures


async def test_an_unknown_conversation_stops_before_the_stream() -> None:
    """Answering into a conversation that cannot hold the reply is pointless."""
    provider, socket = Counting(), FakeSocket()
    streaming = AnswerStreamer(provider, Recording(error=ConversationNotFoundError()))

    await streaming.answer(
        Connection(socket=socket), "why?", conversation_id=uuid.uuid4()
    )

    assert provider.calls == 0, "no model call was made"
    assert socket.kinds == [ServerMessageType.ERROR]
    assert socket.sent[0]["code"] == ConversationNotFoundError.code


async def test_the_connection_is_released_after_a_recording_failure() -> None:
    failing = AnswerStreamer(
        StaticStreamingProvider("x"), Recording(error=ConversationNotFoundError())
    )
    await failing.answer(
        Connection(socket=FakeSocket()), "why?", conversation_id=uuid.uuid4()
    )
    assert failing.busy is False


async def test_a_late_recording_failure_does_not_retract_the_answer() -> None:
    """The deltas already reached the client; the completion still follows."""
    recorder, socket = FailingLate(), FakeSocket()
    await streamer(recorder).answer(
        Connection(socket=socket), "why?", conversation_id=uuid.uuid4()
    )

    assert recorder.turns == [MessageRole.CUSTOMER, MessageRole.ASSISTANT]
    assert ServerMessageType.DELTA in socket.kinds
    assert socket.kinds[-1] == ServerMessageType.COMPLETE
    assert any(
        frame.get("code") == RECORD_FAILED_CODE
        for frame in socket.sent
        if frame["type"] == ServerMessageType.ERROR
    )


async def test_an_unexpected_recording_error_leaks_nothing() -> None:
    socket = FakeSocket()
    await streamer(Recording(error=RuntimeError("postgresql://user:pw@host"))).answer(
        Connection(socket=socket), "why?", conversation_id=uuid.uuid4()
    )
    rendered = json.dumps(socket.sent)
    assert "user:pw" not in rendered
    assert socket.sent[0]["code"] == RECORD_FAILED_CODE


async def test_a_connection_with_no_recorder_says_so() -> None:
    provider, socket = Counting(), FakeSocket()
    await AnswerStreamer(provider).answer(
        Connection(socket=socket), "why?", conversation_id=uuid.uuid4()
    )
    assert socket.sent[0]["code"] == NO_RECORDER_CODE
    assert provider.calls == 0


async def test_an_application_error_keeps_its_own_code() -> None:
    class Custom(AppError):
        status_code = 409
        code = "conversation_closed"
        message = "That conversation is closed."

    socket = FakeSocket()
    await streamer(Recording(error=Custom())).answer(
        Connection(socket=socket), "why?", conversation_id=uuid.uuid4()
    )
    assert socket.sent[0]["code"] == "conversation_closed"


def test_the_recorder_stub_satisfies_the_protocol() -> None:
    assert isinstance(Recording(), TurnRecorder)
