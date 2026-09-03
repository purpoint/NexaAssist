"""Conversations and the messages that make them up."""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.customer import Customer


class MessageRole(StrEnum):
    """Who produced a message."""

    CUSTOMER = "customer"
    ASSISTANT = "assistant"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An ongoing exchange with one customer."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_customer_id", "customer_id"),)

    # RESTRICT, matching tickets: a customer's history must not vanish with
    # their row.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False
    )

    # The subject that created this row, when the deployment scopes by
    # subject. Nullable: rows predating ownership, and every row created by a
    # deployment that does not scope, have no owner. Indexed because every
    # scoped read filters on it.
    owner_subject: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        lazy="raise",
        # A message has no meaning apart from its conversation.
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    customer: Mapped["Customer"] = relationship(lazy="raise")


class ConversationMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One turn in a conversation.

    ``position`` is explicit rather than inferred from ``created_at``:
    timestamps collide at this resolution, and an exchange whose order depends
    on a tie-break is an exchange that will eventually be replayed wrongly.
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (
        CheckConstraint("length(btrim(content)) > 0", name="content_not_blank"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("token_estimate >= 0", name="token_estimate_non_negative"),
        UniqueConstraint(
            "conversation_id", "position", name="uq_conversation_messages_conversation_id_position"
        ),
        Index("ix_conversation_messages_conversation_id_position", "conversation_id", "position"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            native_enum=False,
            length=20,
            name="message_role_valid",
            create_constraint=True,
            values_callable=lambda enum: [m.value for m in enum],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Stored rather than recomputed: trimming a window should not require
    # re-tokenising the whole history on every turn.
    token_estimate: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages", lazy="raise"
    )
