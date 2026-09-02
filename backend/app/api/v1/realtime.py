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

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from starlette.websockets import WebSocketState

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.realtime.connection import Connection, ConnectionRegistry
from app.realtime.envelope import (
    ClientMessageType,
    Error,
    Pong,
    Ready,
    parse_client_message,
)
from app.realtime.errors import RealtimeCapacityError, RealtimeProtocolError

logger = get_logger(__name__)

router = APIRouter(tags=["realtime"])

# Close codes. 1008 is policy violation, 1009 is message-too-big; both are
# standard, so a client library reports them without special-casing us.
CLOSE_AT_CAPACITY = 1008
CLOSE_TOO_LARGE = 1009

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


@router.websocket("/ws")
async def realtime(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
    registry: ConnectionRegistry = Depends(get_registry),
) -> None:
    """Serve one realtime connection for its lifetime."""
    await websocket.accept()
    try:
        connection = registry.add(websocket)
    except RealtimeCapacityError:
        await websocket.close(code=CLOSE_AT_CAPACITY, reason="at capacity")
        return

    try:
        await connection.send(Ready(connection_id=connection.id))
        await _serve(connection, websocket, settings)
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
    connection: Connection, websocket: WebSocket, settings: Settings
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

        await _handle(connection, message)


async def _handle(connection: Connection, message: object) -> None:
    """Dispatch one validated frame."""
    if getattr(message, "type", None) is ClientMessageType.PING:
        await connection.send(Pong())
