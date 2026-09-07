"""Schemas for the conversation endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.conversation import MessageRole
from app.schemas.document import Citation


class ConversationStartRequest(BaseModel):
    """Open a conversation for a customer, identified by address."""

    model_config = ConfigDict(extra="forbid")

    customer_email: EmailStr = Field(
        description="The customer's address. Created on first contact."
    )


class ConversationResponse(BaseModel):
    """A conversation's identity."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    created_at: datetime


class ConversationMessageResponse(BaseModel):
    """One turn."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    position: int = Field(
        description="Explicit order within the conversation, not inferred from time."
    )
    role: MessageRole
    content: str
    created_at: datetime
    citations: list[Citation] = Field(
        default_factory=list,
        description=(
            "Sources behind this turn, as they were when it was sent. Empty "
            "for customer turns, for answers that cited nothing, and for "
            "replies policy rewrote. Recorded rather than resolved, so an "
            "edited document does not change what an old answer claims."
        ),
    )


class ConversationHistoryResponse(BaseModel):
    """A conversation's turns, oldest first."""

    model_config = ConfigDict(frozen=True)

    conversation_id: uuid.UUID
    messages: list[ConversationMessageResponse]
