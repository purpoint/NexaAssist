"""Conversation endpoints: opening one, and reading its turns.

M12 built conversation state with no HTTP surface. This is that surface, and
nothing more: a conversation is opened against a customer, and its turns can be
read back in order.

What this does *not* do is condition the answer on the history. The assistant
records each turn, but the pipeline still classifies and answers one message at
a time -- feeding earlier turns into it would mean changing what M8's handlers
receive, which is a contract from a shipped milestone. Recording continuity is
what a client needs to render a conversation; conditioning on it is a separate
change with its own consequences.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.identity import require_identity
from app.auth.authorization import Authorizer
from app.auth.factory import get_authorizer
from app.auth.identity import RequestIdentity
from app.db.session import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.conversation import (
    ConversationHistoryResponse,
    ConversationMessageResponse,
    ConversationResponse,
    ConversationStartRequest,
)
from app.services.conversation import ConversationService
from app.services.customer import CustomerService

router = APIRouter(prefix="/conversations", tags=["conversations"])

MAX_HISTORY = 500


def get_conversation_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ConversationService:
    return ConversationService(session)


def get_customer_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CustomerService:
    return CustomerService(session)


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a conversation",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."}
    },
)
async def start_conversation(
    payload: ConversationStartRequest,
    conversations: Annotated[ConversationService, Depends(get_conversation_service)],
    customers: Annotated[CustomerService, Depends(get_customer_service)],
    identity: Annotated[RequestIdentity, Depends(require_identity)],
    authorizer: Annotated[Authorizer, Depends(get_authorizer)],
) -> ConversationResponse:
    """Open a conversation, creating the customer on first contact."""
    customer = await customers.get_or_create(payload.customer_email)
    conversation = await conversations.start(
        customer.id, owner_subject=authorizer.owner_for(identity)
    )
    return ConversationResponse.model_validate(conversation)


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    summary="Fetch a conversation",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        404: {
            "model": ErrorResponse,
            "description": (
                "No such conversation — also returned for a conversation owned "
                "by another subject, which must not be distinguishable."
            ),
        },
    },
)
async def read_conversation(
    conversation_id: uuid.UUID,
    conversations: Annotated[ConversationService, Depends(get_conversation_service)],
    identity: Annotated[RequestIdentity, Depends(require_identity)],
    authorizer: Annotated[Authorizer, Depends(get_authorizer)],
) -> ConversationResponse:
    """Return a conversation's identity.

    A client resuming from a stored id needs to know it is still valid before
    it starts rendering, and a 404 here is a cheaper answer than an empty
    message list that looks like a conversation with nothing in it.
    """
    return ConversationResponse.model_validate(
        await conversations.get(conversation_id, scope=authorizer.scope_for(identity))
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=ConversationHistoryResponse,
    summary="Read a conversation's turns",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication is required."},
        404: {
            "model": ErrorResponse,
            "description": (
                "No such conversation — also returned for a conversation owned "
                "by another subject, which must not be distinguishable."
            ),
        },
    },
)
async def read_history(
    conversation_id: uuid.UUID,
    conversations: Annotated[ConversationService, Depends(get_conversation_service)],
    identity: Annotated[RequestIdentity, Depends(require_identity)],
    authorizer: Annotated[Authorizer, Depends(get_authorizer)],
    limit: Annotated[int | None, Query(ge=1, le=MAX_HISTORY)] = None,
) -> ConversationHistoryResponse:
    """Return the turns in reading order.

    ``limit`` returns the most recent turns, still oldest-first: a client
    rendering a long conversation wants the end of it, not the beginning.
    """
    # Resolves to a 404 before any history is read, rather than returning an
    # empty list for a conversation that never existed.
    await conversations.get(conversation_id, scope=authorizer.scope_for(identity))
    messages = await conversations.history(conversation_id, limit=limit)
    return ConversationHistoryResponse(
        conversation_id=conversation_id,
        messages=[ConversationMessageResponse.model_validate(m) for m in messages],
    )
