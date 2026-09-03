"""Customer identity.

Exists because more than one thing now needs a customer: M4 creates one on a
first ticket, and M17 needs one to hang a conversation from. Rather than reach
into ``TicketService`` for a private helper, or widen that class's public
surface after it shipped, identity gets a service of its own.

The get-or-create logic is deliberately the same shape as the one inside
``TicketService``, including the SAVEPOINT: two first contacts from the same
address race, both see no customer, both insert, and the unique constraint
rejects one. A test asserts the two paths agree on the same address, so they
cannot drift apart unnoticed.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Customer

logger = get_logger(__name__)


def normalise_email(email: str) -> str:
    """One spelling per address, so a customer is not created twice."""
    return email.strip().lower()


class CustomerService:
    """Finds or creates the customer behind an email address."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, email: str) -> Customer:
        address = normalise_email(email)
        existing = await self._find(address)
        if existing is not None:
            return existing

        customer = Customer(email=address)
        try:
            # A SAVEPOINT, so losing the race rolls back only this statement
            # and leaves the surrounding work intact.
            async with self._session.begin_nested():
                self._session.add(customer)
        except IntegrityError:
            winner = await self._find(address)
            if winner is None:  # pragma: no cover - only on a genuine conflict
                raise
            return winner

        await self._session.commit()
        # Identifier only: an email address is customer data.
        logger.info("customer created customer_id=%s", customer.id)
        return customer

    async def _find(self, email: str) -> Customer | None:
        return await self._session.scalar(
            select(Customer).where(Customer.email == email)
        )
