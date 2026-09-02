"""Concrete background work, over the services that already exist.

Nothing here reimplements domain logic. Each handler validates a payload and
calls the same service an endpoint would, so the background and foreground
paths cannot drift into two different meanings of "ingest a document".

Handlers do not touch the session. The service owns the transaction boundary
-- both of these already commit -- and the session's lifecycle belongs to
whoever constructed the service, exactly as it does on a request. A handler
that also committed would be a second opinion about a boundary that already
has an owner.
"""

from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.jobs.handlers import JobError
from app.services.document import DocumentService
from app.services.ticket import TicketService

logger = get_logger(__name__)


class IngestDocumentPayload(BaseModel):
    """A document to chunk, embed, and store."""

    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)


class IngestDocumentHandler:
    """Ingestion off the request path.

    The natural first job in this system: chunking and embedding is the
    slowest thing the application does, nothing needs to wait on it, and the
    work is already implemented once in ``DocumentService``.
    """

    name = "ingest_document"
    payload = IngestDocumentPayload

    def __init__(self, documents: DocumentService) -> None:
        self._documents = documents

    async def run(self, params: BaseModel) -> None:
        assert isinstance(params, IngestDocumentPayload)
        try:
            await self._documents.ingest(title=params.title, content=params.content)
        except SQLAlchemyError as exc:
            # Retryable: a dropped connection or a lock timeout is exactly the
            # failure a second attempt fixes. Type only in the log, and nothing
            # about the row in the message -- both are customer content.
            logger.warning("document ingestion failed error=%s", type(exc).__name__)
            raise JobError("The document could not be stored.", retryable=True) from None


class CreateTicketPayload(BaseModel):
    """An inbound message to turn into a ticket."""

    customer_email: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)


class CreateTicketHandler:
    """Ticket creation from an inbound channel.

    Accepting a message and persisting it are different concerns: mail and
    chat gateways deliver in bursts and expect to be released quickly, and a
    ticket that appears a moment later costs nothing.
    """

    name = "create_ticket"
    payload = CreateTicketPayload

    def __init__(self, tickets: TicketService) -> None:
        self._tickets = tickets

    async def run(self, params: BaseModel) -> None:
        assert isinstance(params, CreateTicketPayload)
        try:
            await self._tickets.create(
                customer_email=params.customer_email,
                subject=params.subject,
                body=params.body,
            )
        except SQLAlchemyError as exc:
            logger.warning("ticket creation failed error=%s", type(exc).__name__)
            raise JobError("The ticket could not be created.", retryable=True) from None
