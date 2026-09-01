"""Domain errors raised by the service layer.

Subclasses of :class:`~app.core.exceptions.AppError`, so the handlers
registered in M1 render them through ``ErrorResponse`` without extra wiring.
Messages are generic: a driver or ORM message can quote column values, which
for these tables means customer content.
"""

from app.core.exceptions import NotFoundError


class TicketNotFoundError(NotFoundError):
    """No ticket exists with the requested identifier."""

    code = "ticket_not_found"
    message = "The requested ticket was not found."


class DocumentNotFoundError(NotFoundError):
    """No document exists with the requested identifier."""

    code = "document_not_found"
    message = "The requested document was not found."
