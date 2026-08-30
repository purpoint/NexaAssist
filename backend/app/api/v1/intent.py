"""Intent analysis endpoint.

The route stays thin on purpose: validate the request, hand it to
:class:`~app.services.intent.IntentService`, return the result. The
FastAPI-aware wiring lives here rather than in the service, so the service
carries no web framework dependency.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.llm.base import LLMProvider
from app.llm.factory import get_llm_provider
from app.schemas.intent import IntentAnalysis, IntentAnalysisRequest
from app.services.intent import IntentService

router = APIRouter(prefix="/intent", tags=["intent"])


def get_intent_service(
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
) -> IntentService:
    """Build the service for one request, over the configured provider."""
    return IntentService(provider)


@router.post(
    "/analyze",
    response_model=IntentAnalysis,
    summary="Classify the intent of a customer message",
)
async def analyze_intent(
    payload: IntentAnalysisRequest,
    service: Annotated[IntentService, Depends(get_intent_service)],
) -> IntentAnalysis:
    """Classify a single customer message into one intent category."""
    return await service.analyze(payload.message)
