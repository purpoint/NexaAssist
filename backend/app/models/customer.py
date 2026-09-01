"""The customer a support request comes from.

Deliberately thin: an identity and nothing else. The entity earns its own table
through uniqueness and by being a foreign-key target, not through a field
count. A name, phone number, or organisation would each be a field added
because some later milestone might want it.
"""

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from app.models.ticket import Ticket


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Someone who raises support tickets, identified by email address."""

    __tablename__ = "customers"
    __table_args__ = (
        # Uniqueness has to be case-insensitive in practice. Normalising in the
        # service is a promise; this is the enforcement, and it holds for every
        # writer including migrations and one-off scripts.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("length(btrim(email)) > 0", name="email_not_blank"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)

    # lazy="raise" is load-bearing under asyncio: an accidental lazy load would
    # otherwise raise MissingGreenlet somewhere far from its cause, or quietly
    # work in a sync test and fail in production. This turns it into an
    # immediate error at the access site, forcing callers to eager-load.
    tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="customer", lazy="raise"
    )
