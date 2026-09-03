"""Schemas for the conversation endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.conversation import MessageRole


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


class ConversationHistoryResponse(BaseModel):
    """A conversation's turns, oldest first."""

    model_config = ConfigDict(frozen=True)

    conversation_id: uuid.UUID
    messages: list[ConversationMessageResponse]
