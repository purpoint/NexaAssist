"""Assembling the handoff for one request."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.escalation.criteria import EscalationCriteria
from app.escalation.handoff import HandoffService
from app.services.review import ReviewQueue


def build_handoff(*, session: AsyncSession, settings: Settings) -> HandoffService:
    """Build the handoff service over the review queue."""
    return HandoffService(
        ReviewQueue(session),
        EscalationCriteria(min_confidence=settings.routing_min_confidence),
    )
