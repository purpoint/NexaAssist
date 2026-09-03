"""Recording realtime turns.

A socket lives for minutes; a database session should not. Holding one open for
the life of a connection would pin a pool connection per idle client and keep a
transaction open across long gaps -- so each turn gets its own short-lived
session, opened when there is something to write and closed immediately after.

This is the only module in ``app.realtime`` that knows about the database. The
streamer depends on the :class:`TurnRecorder` protocol, so the transport layer
stays testable without one.
"""

import uuid
from typing import Protocol, runtime_checkable

from app.auth.authorization import OwnerScope
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.models.conversation import MessageRole
from app.services.conversation import ConversationService

logger = get_logger(__name__)


@runtime_checkable
class TurnRecorder(Protocol):
    """Appends one turn to a conversation."""

    async def record(
        self, conversation_id: uuid.UUID, role: MessageRole, content: str
    ) -> None:
        """Store the turn, raising if the conversation does not exist."""
        ...

    def scope_to(self, scope: OwnerScope | None) -> None:
        """Bind the recorder to one subject's resources."""
        ...


class SessionTurnRecorder:
    """Records a turn in its own session, under this connection's ownership.

    The scope arrives at connect time rather than in the constructor, because
    it is not known until the handshake's ticket has said who is asking. Until
    it is set the recorder is unscoped, which is the correct behaviour for a
    deployment that does not scope at all.

    This used to refuse outright whenever a deployment scoped by subject: a
    socket carried no identity, so writing at all would have bypassed the
    ownership the HTTP path enforces. Tickets removed that limitation rather
    than working around it.
    """

    def __init__(self, scope: OwnerScope | None = None) -> None:
        self._scope = scope

    def scope_to(self, scope: OwnerScope | None) -> None:
        """Bind this recorder to one subject's resources for the connection."""
        self._scope = scope

    async def record(
        self, conversation_id: uuid.UUID, role: MessageRole, content: str
    ) -> None:
        factory = get_sessionmaker()
        async with factory() as session:
            # ConversationService owns the transaction, as it does everywhere
            # else; this only decides how long the session lives. The scope
            # makes another subject's conversation a 404, exactly as over HTTP.
            await ConversationService(session).append(
                conversation_id, role=role, content=content, scope=self._scope
            )
