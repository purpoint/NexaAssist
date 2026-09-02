"""Who is currently connected, and how many may be.

A registry rather than a bare set: a long-lived connection is a resource the
process holds until a client goes away, and the number of them a server will
hold has to be a decision somebody made rather than however many arrive.

Deliberately not a pub/sub hub. There is no fan-out here because nothing in the
system needs one yet, and a broadcast mechanism with no subscriber is exactly
the kind of structure that later turns out to have been the wrong shape.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from app.core.logging import get_logger
from app.realtime.errors import RealtimeCapacityError

logger = get_logger(__name__)

DEFAULT_MAX_CONNECTIONS = 100


class Sender(Protocol):
    """The one thing the registry needs of a socket.

    Narrow on purpose: a Starlette ``WebSocket`` satisfies it, and so does a
    test double, without either being named here.
    """

    async def send_text(self, data: str) -> None:
        ...


@dataclass
class Connection:
    """One live client."""

    socket: Sender
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    async def send(self, message: object) -> None:
        """Send a server frame.

        Takes the model and serialises here so no caller can send a shape that
        was never part of the contract.
        """
        payload = getattr(message, "model_dump_json", None)
        if payload is None:  # pragma: no cover - guard against a raw dict
            raise TypeError("Only envelope models may be sent.")
        await self.socket.send_text(payload())


class ConnectionRegistry:
    """The set of live connections, with a ceiling."""

    def __init__(self, *, max_connections: int = DEFAULT_MAX_CONNECTIONS) -> None:
        if max_connections < 1:
            raise ValueError("max_connections must be at least 1.")
        self._max = max_connections
        self._connections: dict[str, Connection] = {}

    def add(self, socket: Sender) -> Connection:
        """Register a socket, refusing once the ceiling is reached."""
        if len(self._connections) >= self._max:
            logger.warning("realtime connection refused reason=at_capacity")
            raise RealtimeCapacityError(details={"limit": self._max})
        connection = Connection(socket=socket)
        self._connections[connection.id] = connection
        logger.info(
            "realtime connected id=%s live=%d", connection.id, len(self._connections)
        )
        return connection

    def remove(self, connection: Connection) -> None:
        """Deregister a socket. Removing one that is already gone is not an error.

        A disconnect can be noticed in more than one place, and a registry that
        raised on the second notice would turn cleanup into a source of errors.
        """
        if self._connections.pop(connection.id, None) is not None:
            logger.info(
                "realtime disconnected id=%s live=%d",
                connection.id,
                len(self._connections),
            )

    def get(self, connection_id: str) -> Connection | None:
        return self._connections.get(connection_id)

    @property
    def capacity(self) -> int:
        return self._max

    def __len__(self) -> int:
        return len(self._connections)

    def __iter__(self) -> Iterator[Connection]:
        return iter(list(self._connections.values()))
