"""Intent analysis: the request the API accepts and the model it returns.

``IntentAnalysis`` serves two roles deliberately -- it is the JSON Schema sent
to the provider *and* the HTTP response model. Keeping one definition means the
wire contract and the model contract cannot drift apart.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IntentCategory(StrEnum):
    """The closed set of intents a customer message can be classified into.

    A closed set is the point: downstream routing becomes a lookup rather than
    fuzzy string matching. ``OTHER`` is mandatory -- without an escape hatch the
    model is forced to miscategorise, and a confident wrong answer is worse than
    an honest "none of these".
    """

    BILLING = "billing"
    TECHNICAL_SUPPORT = "technical_support"
    ACCOUNT = "account"
    PRODUCT_QUESTION = "product_question"
    COMPLAINT = "complaint"
    OTHER = "other"


class IntentAnalysis(BaseModel):
    """The model's classification of a single customer message."""

    intent: IntentCategory = Field(
        description="Closed-set category. 'other' when no category fits.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "The model's SELF-REPORTED confidence, not a calibrated "
            "probability. 0.94 means the model asserted high confidence; it "
            "does not mean the classification is correct 94% of the time. Do "
            "not threshold on this as though it were measured."
        ),
    )
    reason: str = Field(
        min_length=1,
        max_length=280,
        description="One short sentence justifying the chosen intent.",
    )


class IntentAnalysisRequest(BaseModel):
    """The body accepted by ``POST /intent/analyze``."""

    # Unknown fields are rejected rather than ignored, so a typo surfaces as a
    # 422 instead of being silently dropped.
    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=1,
        max_length=8000,
        description="The customer message to classify.",
    )


STATIC_EXAMPLE = IntentAnalysis(
    intent=IntentCategory.OTHER,
    confidence=0.0,
    reason="Static provider response; no model was called.",
)
"""The canned answer served when ``LLM_PROVIDER=static``.

Deliberately ``OTHER`` with zero confidence: it should be obvious at a glance
that no model produced it.
"""
