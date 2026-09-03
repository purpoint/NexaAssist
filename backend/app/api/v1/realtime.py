"""WebSocket endpoint.

Not described by OpenAPI -- FastAPI documents HTTP operations, and a WebSocket
route is not one -- so the contract this endpoint honours is the one written
in ``app.realtime.envelope``, and the tests here are what pin it.

The loop is deliberately dull: accept, register, greet, then read frames until
the client leaves. Everything interesting about a socket is in the failure
modes, and each is handled explicitly rather than by letting an exception unwind
the connection:

* **Over capacity** closes before the handshake completes, so a refused client
  gets a close code rather than a connection that appears to work.
* **An oversized frame** closes with 1009. Answering it would mean having
  already buffered whatever was sent, which is the thing being prevented.
* **A malformed frame** is answered with an error frame and the connection is
  kept. A client bug on one message is not a reason to drop the session.
* **A disconnect** ends the loop quietly. It is how sockets normally end, not
  an error worth logging as one.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from app.api.v1.limits import enforce_rate_limit
from app.auth.authorization import Authorizer
from app.auth.base import Authenticator
from app.auth.factory import get_authenticator, get_authorizer, get_ticket_store
from app.auth.identity import RequestIdentity
from app.auth.tickets import TicketStore
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.llm.streaming import StreamingLLMProvider, build_streaming_provider
from app.realtime.answers import AnswerStreamer
from app.realtime.connection import Connection, ConnectionRegistry
from app.realtime.conversations import SessionTurnRecorder, TurnRecorder
from app.realtime.envelope import (
    Ask,
    ClientMessageType,
    Error,
    Pong,
    Ready,
    parse_client_message,
)
from app.realtime.errors import RealtimeCapacityError, RealtimeProtocolError
from app.schemas.common import ErrorResponse
from app.schemas.realtime import RealtimeTicketResponse

logger = get_logger(__name__)

router = APIRouter(tags=["realtime"])

# Close codes. 1008 is policy violation, 1009 is message-too-big; both are
# standard, so a client library reports them without special-casing us.
CLOSE_AT_CAPACITY = 1008
CLOSE_TOO_LARGE = 1009
CLOSE_UNAUTHENTICATED = 1008

_registry: ConnectionRegistry | None = None


def get_registry() -> ConnectionRegistry:
    """The process-wide connection registry.

    Built once and kept: a capacity ceiling only means anything if every
    connection is counted against the same one. A FastAPI dependency rather
    than a module lookup so a test can substitute a registry with a different
    ceiling, the way the LLM provider and embedder are already substituted.
    """
    global _registry
    if _registry is None:
        _registry = ConnectionRegistry(
            max_connections=get_settings().realtime_max_connections
        )
    return _registry


def reset_registry() -> None:
    """Drop the cached registry. For tests that need a fresh ceiling."""
    global _registry
    _registry = None


def get_streaming_provider() -> StreamingLLMProvider:
    """The streaming backend this endpoint answers from.

    A dependency so a test substitutes a deterministic provider, exactly as it
    already substitutes the completion provider and the embedder.
    """
    return build_streaming_provider(get_settings())


def get_turn_recorder() -> TurnRecorder:
    """How realtime turns reach the database.

    A dependency so a test can substitute one, and so the socket never holds a
    session: the recorder opens a short-lived one per turn. It is handed the
    scope for *this* connection at connect time, once the ticket has said who
    is asking.
    """
    return SessionTurnRecorder()


@router.post(
    "/ws/ticket",
    response_model=RealtimeTicketResponse,
    summary="Mint a ticket for a WebSocket handshake",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
    },
)
async def issue_ticket(
    identity: Annotated[RequestIdentity, Depends(enforce_rate_limit)],
    tickets: Annotated[TicketStore, Depends(get_ticket_store)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RealtimeTicketResponse:
    """Exchange an authenticated HTTP request for a short-lived ticket.

    This is the only place the two credentials meet, and they meet over HTTP
    where a header carries the key. The ticket is what reaches the URL, and it
    is short-lived, single-use, opaque and bound to this subject -- so the key
    itself never appears in a query string, a proxy log or a browser history.
    """
    return RealtimeTicketResponse(
        ticket=await tickets.issue(identity.subject),
        expires_in_seconds=settings.realtime_ticket_ttl_seconds,
    )


async def authenticate_socket(
    ticket: str | None,
    authenticator: Authenticator,
    tickets: TicketStore,
) -> RequestIdentity | None:
    """Resolve the identity behind a handshake, or ``None`` to refuse it.

    An open deployment ignores the ticket entirely, which is what keeps a
    socket working exactly as it did before this existed. A protected one
    requires a valid ticket and treats unknown, expired and already-spent the
    same way -- distinguishing them would tell an attacker which guesses were
    once real.
    """
    if not authenticator.protects:
        return RequestIdentity.anonymous()

    if not ticket:
        logger.info("realtime handshake refused reason=missing_ticket")
        return None

    subject = await tickets.redeem(ticket)
    if subject is None:
        # No fingerprint: a ticket is single-use, so correlating attempts by
        # value would only ever match one real request.
        logger.warning("realtime handshake refused reason=invalid_ticket")
        return None

    logger.info("realtime authenticated subject=%s", subject)
    return RequestIdentity.api_key(subject)


@router.websocket("/ws")
async def realtime(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
    registry: ConnectionRegistry = Depends(get_registry),
    provider: StreamingLLMProvider = Depends(get_streaming_provider),
    recorder: TurnRecorder = Depends(get_turn_recorder),
    authenticator: Authenticator = Depends(get_authenticator),
    authorizer: Authorizer = Depends(get_authorizer),
    tickets: TicketStore = Depends(get_ticket_store),
    ticket: str | None = Query(
        default=None,
        description=(
            "A ticket from POST /ws/ticket. Required when the deployment "
            "authenticates; ignored when it does not."
        ),
    ),
) -> None:
    """Serve one realtime connection for its lifetime."""
    await websocket.accept()

    identity = await authenticate_socket(ticket, authenticator, tickets)
    if identity is None:
        # Closed after accepting, because a refusal before the handshake gives
        # a browser no code to act on.
        await websocket.close(
            code=CLOSE_UNAUTHENTICATED, reason="a valid ticket is required"
        )
        return
    try:
        connection = registry.add(websocket)
    except RealtimeCapacityError:
        await websocket.close(code=CLOSE_AT_CAPACITY, reason="at capacity")
        return

    try:
        await connection.send(Ready(connection_id=connection.id))
        # One streamer per connection: that is the scope the single-flight rule
        # is defined over.
        # The recorder is scoped to whoever the ticket named, so a realtime
        # turn is written under the same ownership rules as an HTTP one.
        recorder.scope_to(authorizer.scope_for(identity))
        await _serve(
            connection, websocket, settings, AnswerStreamer(provider, recorder)
        )
    except WebSocketDisconnect:
        # The ordinary way a socket ends.
        pass
    finally:
        registry.remove(connection)
        # ``application_state``, not ``client_state``: this asks whether *we*
        # have already sent a close frame. The oversize path sends one and
        # returns, and closing a second time raises.
        if websocket.application_state is WebSocketState.CONNECTED:
            await websocket.close()


async def _serve(
    connection: Connection,
    websocket: WebSocket,
    settings: Settings,
    streamer: AnswerStreamer,
) -> None:
    """Read and answer frames until the client goes away."""
    while True:
        raw = await websocket.receive_text()

        if len(raw.encode("utf-8")) > settings.realtime_max_message_bytes:
            # Measured in bytes, not characters: the limit protects memory,
            # and a multi-byte character costs more than one of either.
            logger.warning(
                "realtime frame rejected id=%s reason=too_large", connection.id
            )
            await websocket.close(code=CLOSE_TOO_LARGE, reason="message too large")
            return

        try:
            message = parse_client_message(raw)
        except ValidationError:
            # Field paths are not echoed: they would quote the frame back, and
            # the frame is client data.
            logger.info(
                "realtime frame rejected id=%s reason=malformed", connection.id
            )
            await connection.send(
                Error(
                    code=RealtimeProtocolError.code,
                    message=RealtimeProtocolError.message,
                )
            )
            continue

        await _handle(connection, message, streamer)


async def _handle(
    connection: Connection, message: object, streamer: AnswerStreamer
) -> None:
    """Dispatch one validated frame."""
    if isinstance(message, Ask):
        await streamer.answer(
            connection, message.question, conversation_id=message.conversation_id
        )
        return
    if getattr(message, "type", None) is ClientMessageType.PING:
        await connection.send(Pong())
