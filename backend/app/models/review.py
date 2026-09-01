"""The queue of requests waiting for a person."""

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ReviewStatus(StrEnum):
    """Where an item sits in the queue."""

    PENDING = "pending"
    CLAIMED = "claimed"
    RESOLVED = "resolved"


class ReviewItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One request handed to a human.

    Stores the customer's message and the reply the system would have sent, so
    a reviewer can see what nearly went out without replaying the run. The
    escalation reason is recorded because triage starts with "why is this
    here".
    """

    __tablename__ = "review_items"
    __table_args__ = (
        CheckConstraint("length(btrim(message)) > 0", name="message_not_blank"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        # The queue is read newest-first, and almost always filtered to
        # pending, so the composite serves the query reviewers actually make.
        Index("ix_review_items_status_created_at", "status", "created_at"),
        Index("ix_review_items_ticket_id", "ticket_id"),
    )

    # Optional: a request may be escalated before any ticket exists for it.
    # SET NULL rather than CASCADE -- losing the ticket must not erase the
    # record that a human was asked to look at something.
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True
    )

    message: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)

    status: Mapped[ReviewStatus] = mapped_column(
        Enum(
            ReviewStatus,
            native_enum=False,
            length=20,
            name="review_status_valid",
            create_constraint=True,
            values_callable=lambda enum: [m.value for m in enum],
        ),
        nullable=False,
        server_default=ReviewStatus.PENDING.value,
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)

    ticket = relationship("Ticket", lazy="raise")
