"""Ticket application logic.

Depends on an :class:`~sqlalchemy.ext.asyncio.AsyncSession` and nothing else --
no FastAPI, no provider SDK -- so a later orchestration layer can call it
directly and a test can drive it without HTTP.

The service owns the transaction boundary. The session dependency deliberately
does not commit (see ``app.db.session``), because only the caller knows what
constitutes one complete business operation; here that is "a ticket exists,
along with the customer it belongs to".
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Customer, Ticket, TicketStatus
from app.services.errors import TicketNotFoundError

logger = get_logger(__name__)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def normalise_email(email: str) -> str:
    """Lowercase and trim, matching the database check constraints."""
    return email.strip().lower()


class TicketService:
    """Creates and reads support tickets."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, customer_email: str, subject: str, body: str) -> Ticket:
        """Record a ticket, creating the customer on first contact."""
        customer = await self._get_or_create_customer(normalise_email(customer_email))

        ticket = Ticket(customer_id=customer.id, subject=subject, body=body)
        self._session.add(ticket)
        await self._session.flush()
        await self._session.commit()

        # Identifiers and status only. The subject, the body, and the customer's
        # email address are all customer content.
        logger.info(
            "ticket created ticket_id=%s customer_id=%s status=%s",
            ticket.id,
            ticket.customer_id,
            ticket.status.value,
        )
        return ticket

    async def get(self, ticket_id: uuid.UUID) -> Ticket:
        """Return one ticket, or raise :class:`TicketNotFoundError`."""
        ticket = await self._session.get(Ticket, ticket_id)
        if ticket is None:
            raise TicketNotFoundError(details={"ticket_id": str(ticket_id)})
        return ticket

    async def list(
        self,
        *,
        status: TicketStatus | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> Sequence[Ticket]:
        """Return a page of tickets, newest first."""
        statement = select(Ticket)
        if status is not None:
            statement = statement.where(Ticket.status == status)

        # id is the tiebreaker: timestamps collide at this resolution, and an
        # unstable sort makes pagination skip or repeat rows between pages.
        statement = statement.order_by(Ticket.created_at.desc(), Ticket.id.desc())
        statement = statement.limit(min(limit, MAX_PAGE_SIZE)).offset(offset)

        return (await self._session.scalars(statement)).all()

    async def _get_or_create_customer(self, email: str) -> Customer:
        """Find the customer, or insert them.

        Two first-time tickets from the same address race here: both see no
        customer and both insert, and the unique constraint rejects one. The
        insert runs inside a SAVEPOINT so that losing the race rolls back only
        that statement, leaving the surrounding work intact.
        """
        existing = await self._find_customer(email)
        if existing is not None:
            return existing

        customer = Customer(email=email)
        try:
            async with self._session.begin_nested():
                self._session.add(customer)
        except IntegrityError:
            winner = await self._find_customer(email)
            if winner is None:  # pragma: no cover - only on a genuine conflict
                raise
            return winner
        return customer

    async def _find_customer(self, email: str) -> Customer | None:
        return await self._session.scalar(
            select(Customer).where(Customer.email == email)
        )
