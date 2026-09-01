"""The human review queue.

Depends on a session and nothing else. The service owns the transaction
boundary, as elsewhere: an item is queued only when it is durably queued.

Claiming is deliberately conditional on the item still being pending, so two
reviewers opening the queue at the same moment cannot both take the same item.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.escalation.criteria import EscalationReason
from app.models import ReviewItem, ReviewStatus
from app.schemas.intent import IntentCategory
from app.services.errors import ReviewItemNotFoundError

logger = get_logger(__name__)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class ReviewQueue:
    """Records escalated requests and tracks what a human did with them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        message: str,
        intent: IntentCategory,
        reason: EscalationReason,
        proposed_reply: str | None = None,
        ticket_id: uuid.UUID | None = None,
    ) -> ReviewItem:
        """Add a request to the queue."""
        item = ReviewItem(
            message=message,
            intent=intent.value,
            reason=reason.value,
            proposed_reply=proposed_reply,
            ticket_id=ticket_id,
        )
        self._session.add(item)
        await self._session.flush()
        await self._session.commit()

        # Identifiers and the reason only; the message and reply are customer
        # content and are stored, not logged.
        logger.info(
            "escalated to review review_id=%s intent=%s reason=%s ticket_id=%s",
            item.id,
            intent.value,
            reason.value,
            ticket_id or "-",
        )
        return item

    async def get(self, review_id: uuid.UUID) -> ReviewItem:
        item = await self._session.get(ReviewItem, review_id)
        if item is None:
            raise ReviewItemNotFoundError(details={"review_id": str(review_id)})
        return item

    async def pending(
        self, *, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0
    ) -> Sequence[ReviewItem]:
        """Oldest first: a queue is worked front to back."""
        statement = (
            select(ReviewItem)
            .where(ReviewItem.status == ReviewStatus.PENDING)
            .order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc())
            .limit(min(limit, MAX_PAGE_SIZE))
            .offset(offset)
        )
        return (await self._session.scalars(statement)).all()

    async def claim(self, review_id: uuid.UUID) -> ReviewItem | None:
        """Take an item, or return ``None`` if someone else already has.

        The status check is part of the UPDATE rather than a prior SELECT, so
        two reviewers racing cannot both succeed.
        """
        statement = (
            update(ReviewItem)
            .where(
                ReviewItem.id == review_id,
                ReviewItem.status == ReviewStatus.PENDING,
            )
            .values(status=ReviewStatus.CLAIMED)
            .returning(ReviewItem.id)
        )
        claimed = (await self._session.execute(statement)).scalar_one_or_none()
        await self._session.commit()

        if claimed is None:
            return None
        logger.info("review claimed review_id=%s", review_id)
        return await self.get(review_id)

    async def resolve(self, review_id: uuid.UUID, *, resolution: str) -> ReviewItem:
        """Close an item with what the reviewer decided."""
        item = await self.get(review_id)
        item.status = ReviewStatus.RESOLVED
        item.resolution = resolution
        await self._session.commit()

        logger.info("review resolved review_id=%s", review_id)
        return item

    async def counts(self) -> dict[str, int]:
        """How many items sit in each status."""
        from sqlalchemy import func

        rows = await self._session.execute(
            select(ReviewItem.status, func.count()).group_by(ReviewItem.status)
        )
        tallied = {status.value: count for status, count in rows}
        return {s.value: tallied.get(s.value, 0) for s in ReviewStatus}
