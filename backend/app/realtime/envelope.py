"""The WebSocket wire contract.

Every frame in both directions is a validated envelope, never free-form JSON.
A socket that accepts whatever arrives is an undocumented API that changes
shape whenever a caller does, and it is the one part of the surface FastAPI
will not describe in OpenAPI -- so if the contract is not written down here,
it is not written down anywhere.

Frames are small and typed rather than one permissive object with optional
fields everywhere: a discriminated union means an unknown ``type`` is rejected
at the edge instead of falling through to a handler that quietly does nothing.
"""

import uuid
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

MAX_MESSAGE_BYTES = 64 * 1024
"""Largest frame accepted from a client.

An unbounded frame is a memory-exhaustion vector: the server would buffer
whatever a client chose to send before it could decide the frame was
unreasonable. Generous for text, far below anything that threatens the process.
"""


MAX_QUESTION_LENGTH = 4_000
"""Longest question accepted on a socket.

Well inside the frame ceiling, and bounded separately because the two limits
protect different things: the frame limit protects memory, this one protects
the model call the question turns into.
"""


class ClientMessageType(StrEnum):
    """What a client may send."""

    PING = "ping"
    ASK = "ask"


class ServerMessageType(StrEnum):
    """What the server may send."""

    READY = "ready"
    PONG = "pong"
    DELTA = "delta"
    COMPLETE = "complete"
    ERROR = "error"


class Ping(BaseModel):
    """A liveness check. Answered with :class:`Pong`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal[ClientMessageType.PING]


class Ask(BaseModel):
    """A question to answer, streamed back as deltas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal[ClientMessageType.ASK]
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Record this exchange against an existing conversation, the same "
            "way POST /assistant/messages does. Optional: a socket with no "
            "conversation still gets an answer."
        ),
    )


ClientMessage = Annotated[Ping | Ask, Field(discriminator="type")]
"""Every frame the server will accept. Anything else is a protocol error."""

CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


class ServerMessage(BaseModel):
    """Base for everything the server sends."""

    model_config = ConfigDict(frozen=True)

    type: ServerMessageType


class Ready(ServerMessage):
    """Sent once, immediately after the connection is accepted.

    Gives a client something definite to wait for. Without it the only signal
    that the server is listening is the absence of a close frame, which is not
    a signal at all.
    """

    type: Literal[ServerMessageType.READY] = ServerMessageType.READY
    connection_id: str
    protocol_version: int = 1


class Pong(ServerMessage):
    """The answer to a ping."""

    type: Literal[ServerMessageType.PONG] = ServerMessageType.PONG


class Delta(ServerMessage):
    """One fragment of an answer, in order.

    Concatenating every delta of a stream reproduces the answer exactly. A
    client may render them as they arrive or ignore them entirely and wait for
    :class:`Complete`.
    """

    type: Literal[ServerMessageType.DELTA] = ServerMessageType.DELTA
    text: str


class Complete(ServerMessage):
    """Sent once when a stream ends normally.

    Carries the assembled answer as well as the delta count. That repeats what
    the deltas already said, and does so deliberately: a client that renders
    incrementally can reconcile against it, and one that does not can skip the
    deltas altogether rather than reimplementing the concatenation rule.
    """

    type: Literal[ServerMessageType.COMPLETE] = ServerMessageType.COMPLETE
    text: str
    deltas: int
    conversation_id: uuid.UUID | None = Field(
        default=None,
        description="The conversation this exchange was recorded against, if any.",
    )


class Error(ServerMessage):
    """Something the client sent could not be used.

    Carries the same ``code``/``message`` shape as the HTTP error body, so a
    client parses one error format across both transports. ``details`` is a
    whitelist for the same reason it is over HTTP: never a repr, never a
    traceback, never the offending value.
    """

    type: Literal[ServerMessageType.ERROR] = ServerMessageType.ERROR
    code: str
    message: str
    details: dict[str, Any] | None = None


def parse_client_message(raw: str) -> ClientMessage:
    """Validate one inbound frame, raising ``ValidationError`` if it is wrong."""
    return CLIENT_MESSAGE_ADAPTER.validate_json(raw)
