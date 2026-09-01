"""Ticket endpoints.

Thin by design: validate, delegate to :class:`~app.services.ticket.TicketService`,
return. The FastAPI wiring lives here so the service stays framework-free.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.ticket import TicketStatus
from app.schemas.common import ErrorResponse
from app.schemas.ticket import TicketCreateRequest, TicketListResponse, TicketResponse
from app.services.ticket import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])


def get_ticket_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> TicketService:
    """Build the service over the request-scoped session."""
    return TicketService(session)


@router.post(
    "",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Raise a support ticket",
)
async def create_ticket(
    payload: TicketCreateRequest,
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    """Record a ticket, creating the customer on first contact."""
    ticket = await service.create(
        customer_email=payload.customer_email,
        subject=payload.subject,
        body=payload.body,
    )
    return TicketResponse.model_validate(ticket)


@router.get(
    "/{ticket_id}",
    response_model=TicketResponse,
    summary="Fetch one ticket",
    responses={404: {"model": ErrorResponse, "description": "No such ticket."}},
)
async def get_ticket(
    ticket_id: uuid.UUID,
    service: Annotated[TicketService, Depends(get_ticket_service)],
) -> TicketResponse:
    """Return a single ticket by identifier."""
    return TicketResponse.model_validate(await service.get(ticket_id))


@router.get("", response_model=TicketListResponse, summary="List tickets")
async def list_tickets(
    service: Annotated[TicketService, Depends(get_ticket_service)],
    status_filter: Annotated[
        TicketStatus | None, Query(alias="status", description="Restrict to one status.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TicketListResponse:
    """Return a page of tickets, newest first."""
    tickets = await service.list(status=status_filter, limit=limit, offset=offset)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in tickets],
        limit=limit,
        offset=offset,
    )
