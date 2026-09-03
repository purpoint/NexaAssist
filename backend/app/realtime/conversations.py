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

from app.auth.authorization import Authorizer
from app.core.exceptions import AppError
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


class RealtimeScopingUnsupportedError(AppError):
    """Conversations cannot be recorded over an unauthenticated socket.

    The HTTP path establishes an identity from a header; a WebSocket does not
    carry one, so there is no subject to scope a write by. Rather than write
    unscoped -- which would let any socket append to any subject's
    conversation, bypassing the ownership the HTTP path enforces -- the write
    is refused.

    Fail closed, and deliberately the same client-facing code and message as a
    connection with no recorder at all: a client cannot act on the difference.
    Authenticating the socket is the fix, and it needs a credential transport
    a browser can actually use, which is a separate decision.
    """

    status_code = 403
    code = "realtime_conversations_unavailable"
    message = "This connection cannot record conversations."


class SessionTurnRecorder:
    """Records a turn in its own session.

    Refuses outright when the deployment scopes by subject, because a socket
    has no identity to scope by.
    """

    def __init__(self, authorizer: Authorizer | None = None) -> None:
        self._authorizer = authorizer

    async def record(
        self, conversation_id: uuid.UUID, role: MessageRole, content: str
    ) -> None:
        if self._authorizer is not None and self._authorizer.scopes:
            logger.warning(
                "realtime turn refused conversation_id=%s reason=scoped_authorization",
                conversation_id,
            )
            raise RealtimeScopingUnsupportedError()

        factory = get_sessionmaker()
        async with factory() as session:
            # ConversationService owns the transaction, as it does everywhere
            # else; this only decides how long the session lives.
            await ConversationService(session).append(
                conversation_id, role=role, content=content
            )
