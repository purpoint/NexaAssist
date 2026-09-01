"""Tools over the existing domain.

Concrete capabilities built on M4's tickets and M5's knowledge base. They hold
a service, not a session or a provider, so the tool layer inherits the
transaction and logging rules already established rather than restating them.

Each tool returns plain data -- dicts and lists, not ORM objects. A result may
be serialised into a prompt or a response, and a lazily-loaded relationship
reaching that far would raise at exactly the wrong moment.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.services.document import DocumentService
from app.services.errors import TicketNotFoundError
from app.services.ticket import TicketService
from app.tools.base import ToolError

MAX_SEARCH_RESULTS = 10


class LookupTicketParams(BaseModel):
    ticket_id: uuid.UUID = Field(description="Identifier of the ticket to fetch.")


class LookupTicketTool:
    """Fetch one ticket by identifier."""

    name = "lookup_ticket"
    description = "Look up a single support ticket by its identifier."
    parameters = LookupTicketParams

    def __init__(self, tickets: TicketService) -> None:
        self._tickets = tickets

    async def run(self, params: LookupTicketParams) -> dict[str, Any]:
        try:
            ticket = await self._tickets.get(params.ticket_id)
        except TicketNotFoundError:
            # An expected outcome, not a crash: the caller is told plainly.
            raise ToolError(f"No ticket exists with id {params.ticket_id}.") from None

        return {
            "id": str(ticket.id),
            "customer_id": str(ticket.customer_id),
            "subject": ticket.subject,
            "body": ticket.body,
            "status": ticket.status.value,
            "created_at": ticket.created_at.isoformat(),
        }


class ListTicketsParams(BaseModel):
    status: str | None = Field(
        default=None, description="Restrict to one status: open, pending, resolved, closed."
    )
    limit: int = Field(default=5, ge=1, le=20)


class ListTicketsTool:
    """List recent tickets, optionally filtered by status."""

    name = "list_tickets"
    description = "List recent support tickets, newest first, optionally by status."
    parameters = ListTicketsParams

    def __init__(self, tickets: TicketService) -> None:
        self._tickets = tickets

    async def run(self, params: ListTicketsParams) -> list[dict[str, Any]]:
        from app.models import TicketStatus

        status = None
        if params.status is not None:
            try:
                status = TicketStatus(params.status)
            except ValueError:
                raise ToolError(
                    f"Unknown status {params.status!r}. Valid values: "
                    + ", ".join(s.value for s in TicketStatus)
                ) from None

        tickets = await self._tickets.list(status=status, limit=params.limit)
        return [
            {
                "id": str(t.id),
                "subject": t.subject,
                "status": t.status.value,
                "created_at": t.created_at.isoformat(),
            }
            for t in tickets
        ]


class SearchKnowledgeBaseParams(BaseModel):
    query: str = Field(min_length=1, max_length=1_000, description="What to look for.")
    top_k: int = Field(default=4, ge=1, le=MAX_SEARCH_RESULTS)


class SearchKnowledgeBaseTool:
    """Find documentation passages relevant to a query."""

    name = "search_knowledge_base"
    description = (
        "Search the documentation and return the most relevant passages, "
        "each with the document it came from."
    )
    parameters = SearchKnowledgeBaseParams

    def __init__(self, documents: DocumentService) -> None:
        self._documents = documents

    async def run(self, params: SearchKnowledgeBaseParams) -> list[dict[str, Any]]:
        hits = await self._documents.search(params.query, top_k=params.top_k)
        return [
            {
                "document_id": str(hit.document_id),
                "document_title": hit.document_title,
                "ordinal": hit.ordinal,
                "excerpt": hit.content,
                "similarity": round(hit.similarity, 4),
            }
            for hit in hits
        ]
