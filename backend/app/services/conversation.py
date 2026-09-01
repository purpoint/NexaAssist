"""Conversation history.

Owns the transaction boundary, as elsewhere. Positions are assigned by the
service rather than by the caller: an exchange whose ordering depends on
whoever happened to append last is an exchange that will eventually replay out
of order.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Conversation, ConversationMessage, MessageRole
from app.services.errors import ConversationNotFoundError

logger = get_logger(__name__)

CHARS_PER_TOKEN = 4
"""Rough characters-per-token ratio for the stored estimate.

An estimate, and named as one. Trimming a window needs a cheap, stable measure
available without a tokeniser; being slightly conservative costs a little
context, whereas being wrong in the other direction costs a failed request.
"""


def estimate_tokens(text: str) -> int:
    """A deliberately conservative token estimate."""
    return max(1, -(-len(text) // CHARS_PER_TOKEN))  # ceiling division


class ConversationService:
    """Starts conversations and appends turns to them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, customer_id: uuid.UUID) -> Conversation:
        conversation = Conversation(customer_id=customer_id)
        self._session.add(conversation)
        await self._session.flush()
        await self._session.commit()

        logger.info(
            "conversation started conversation_id=%s customer_id=%s",
            conversation.id,
            customer_id,
        )
        return conversation

    async def get(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self._session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(
                details={"conversation_id": str(conversation_id)}
            )
        return conversation

    async def append(
        self, conversation_id: uuid.UUID, *, role: MessageRole, content: str
    ) -> ConversationMessage:
        """Add a turn at the next position.

        The position comes from the current maximum rather than a caller-
        supplied index, and the unique constraint on
        ``(conversation_id, position)`` rejects a duplicate if two appends
        race, rather than silently interleaving them.
        """
        await self.get(conversation_id)  # 404 rather than a foreign-key error

        message = ConversationMessage(
            conversation_id=conversation_id,
            position=await self._next_position(conversation_id),
            role=role,
            content=content,
            token_estimate=estimate_tokens(content),
        )
        self._session.add(message)
        await self._session.flush()
        await self._session.commit()

        # Position and size only; the content is customer material.
        logger.info(
            "conversation appended conversation_id=%s position=%d role=%s tokens=%d",
            conversation_id,
            message.position,
            role.value,
            message.token_estimate,
        )
        return message

    async def history(
        self, conversation_id: uuid.UUID, *, limit: int | None = None
    ) -> Sequence[ConversationMessage]:
        """Messages in order. ``limit`` returns the most recent, still ordered."""
        if limit is None:
            statement = (
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.position.asc())
            )
            return (await self._session.scalars(statement)).all()

        # Take the newest N in the database, then restore reading order.
        recent = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.position.desc())
            .limit(limit)
            .subquery()
        )
        aliased = select(ConversationMessage).from_statement(
            select(ConversationMessage)
            .where(ConversationMessage.id.in_(select(recent.c.id)))
            .order_by(ConversationMessage.position.asc())
        )
        return (await self._session.scalars(aliased)).all()

    async def _next_position(self, conversation_id: uuid.UUID) -> int:
        highest = await self._session.scalar(
            select(func.max(ConversationMessage.position)).where(
                ConversationMessage.conversation_id == conversation_id
            )
        )
        return 0 if highest is None else highest + 1
