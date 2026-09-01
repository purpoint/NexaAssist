"""Ticket API contract.

``TicketStatus`` is imported from the model: the domain owns its states and the
schema reflects them, never the other way round.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.ticket import TicketStatus

MAX_SUBJECT = 200
MAX_BODY = 20_000


class TicketCreateRequest(BaseModel):
    """Body accepted by ``POST /tickets``."""

    model_config = ConfigDict(extra="forbid")

    customer_email: EmailStr = Field(
        description="Identifies the customer; created on first contact."
    )
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT)
    body: str = Field(min_length=1, max_length=MAX_BODY)


class TicketResponse(BaseModel):
    """One ticket, as returned by the API.

    Carries ``customer_id`` rather than the customer itself: the relationship
    is deliberately ``lazy="raise"``, and nothing here needs it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    subject: str
    body: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class TicketListResponse(BaseModel):
    """A page of tickets.

    No total count: a COUNT over the table on every page is a real cost and no
    caller needs it yet.
    """

    items: list[TicketResponse]
    limit: int
    offset: int
