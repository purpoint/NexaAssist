"""A support request raised by a customer."""

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.customer import Customer


class TicketStatus(StrEnum):
    """Where a ticket sits in its lifecycle.

    A closed set, so downstream handling is a lookup rather than string
    matching. Which transitions are legal is a policy question and belongs to
    a later milestone; this only says which values exist.
    """

    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Ticket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One support request."""

    __tablename__ = "tickets"
    __table_args__ = (
        CheckConstraint("length(btrim(subject)) > 0", name="subject_not_blank"),
        CheckConstraint("length(btrim(body)) > 0", name="body_not_blank"),
        # Serves the default listing: newest first.
        Index("ix_tickets_created_at", "created_at"),
        # Serves the status-filtered listing. Named explicitly because the
        # project convention derives an index name from its first column alone,
        # which would collide with a plain status index.
        Index("ix_tickets_status_created_at", "status", "created_at"),
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        # RESTRICT, not CASCADE: removing a customer must never quietly destroy
        # their support history. There is no delete endpoint, so this governs
        # direct database work -- exactly where a safe default earns its keep.
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # The subject that created this row, when the deployment scopes by
    # subject. Nullable: rows predating ownership, and every row created by a
    # deployment that does not scope, have no owner. Indexed because every
    # scoped read filters on it.
    owner_subject: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )

    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # native_enum=False gives VARCHAR + a CHECK constraint rather than a
    # PostgreSQL ENUM type. Native enums need ALTER TYPE ... ADD VALUE to
    # extend and are close to impossible to shrink; a check constraint is a
    # one-line migration. values_callable stores the lowercase values rather
    # than SQLAlchemy's default of the member names.
    status: Mapped[TicketStatus] = mapped_column(
        Enum(
            TicketStatus,
            native_enum=False,
            length=20,
            name="status_valid",
            # Not the default since SQLAlchemy 1.4: without it, native_enum=False
            # yields a bare VARCHAR and the allowed values are enforced nowhere.
            create_constraint=True,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        server_default=TicketStatus.OPEN.value,
    )

    customer: Mapped["Customer"] = relationship(
        back_populates="tickets", lazy="raise"
    )
