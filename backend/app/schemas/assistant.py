"""Schemas for the assistant endpoint.

Separate from :mod:`app.services.assistant` on purpose. The service's
``AssistantReply`` is a domain value that may grow fields the API has no reason
to publish; the response model here is the contract, and a client depends on
it. Letting one object be both is how internal changes become breaking changes.
"""

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.escalation.criteria import EscalationReason
from app.routing.router import RouteReason
from app.schemas.intent import IntentCategory

MAX_MESSAGE_LENGTH = 8000


class AssistantMessageRequest(BaseModel):
    """A customer message to answer."""

    # Unknown fields are rejected rather than ignored, matching the intent
    # endpoint: a typo should surface as a 422, not vanish.
    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_LENGTH,
        description="The customer message to answer.",
    )


class AssistantMessageResponse(BaseModel):
    """The assistant's reply, with the decision trail that produced it."""

    model_config = ConfigDict(frozen=True)

    reply: str = Field(description="What to say to the customer.")
    intent: IntentCategory = Field(description="The classified category.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "The model's own confidence in the classification. Self-reported, "
            "not calibrated — useful for triage, not a probability."
        ),
    )
    handler: str = Field(description="Which capability produced the reply.")
    route_reason: RouteReason = Field(
        description="Why that handler was chosen, including why a fallback was used."
    )
    fallback: bool = Field(description="True when no specialised handler was used.")
    handled: bool = Field(
        description="False when the request was answered but not resolved."
    )
    policy_modified: bool = Field(
        default=False, description="True when policy changed what would have been sent."
    )
    policy_rule: str | None = Field(
        default=None, description="The policy rule that decided the final reply."
    )
    escalated: bool = Field(
        default=False, description="True when a person has been asked to look."
    )
    escalation_reasons: list[EscalationReason] = Field(
        default_factory=list, description="Why it was escalated, in order."
    )
    review_id: uuid.UUID | None = Field(
        default=None, description="The queued review item, when one was created."
    )
    trace_id: str | None = Field(
        default=None,
        description=(
            "Correlates this response with the server-side trace. Safe to quote "
            "in a support ticket; it identifies the request, not its content."
        ),
    )
