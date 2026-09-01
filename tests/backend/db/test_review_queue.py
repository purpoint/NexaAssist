"""The human review queue against real PostgreSQL."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.escalation.criteria import EscalationReason
from app.models import ReviewStatus
from app.schemas.intent import IntentCategory
from app.services.errors import ReviewItemNotFoundError
from app.services.review import ReviewQueue
from app.services.ticket import TicketService

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        assert await opened.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE
        yield opened
    await engine.dispose()


@pytest.fixture
def queue(session: AsyncSession) -> ReviewQueue:
    return ReviewQueue(session)


async def add(queue: ReviewQueue, message: str = "why was I charged twice", **kw):
    return await queue.enqueue(
        message=message,
        intent=kw.get("intent", IntentCategory.BILLING),
        reason=kw.get("reason", EscalationReason.UNRESOLVED),
        proposed_reply=kw.get("proposed_reply"),
        ticket_id=kw.get("ticket_id"),
    )


# --------------------------------------------------------------------------
# Enqueue
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_enqueue_stores_a_pending_item(queue: ReviewQueue) -> None:
    item = await add(queue, proposed_reply="I have refunded you.")

    assert isinstance(item.id, uuid.UUID)
    assert item.status is ReviewStatus.PENDING
    assert item.reason == "unresolved"
    assert item.proposed_reply == "I have refunded you."


@pytest.mark.anyio
async def test_enqueue_commits(queue: ReviewQueue, test_database_url: str) -> None:
    item = await add(queue)

    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            found = await c.scalar(
                text("SELECT count(*) FROM review_items WHERE id=:i"), {"i": item.id}
            )
    finally:
        await engine.dispose()

    assert found == 1


@pytest.mark.anyio
async def test_an_item_may_reference_a_ticket(
    queue: ReviewQueue, session: AsyncSession
) -> None:
    ticket = await TicketService(session).create(
        customer_email="a@example.com", subject="s", body="b"
    )

    item = await add(queue, ticket_id=ticket.id)

    assert item.ticket_id == ticket.id


@pytest.mark.anyio
async def test_an_item_may_exist_before_any_ticket(queue: ReviewQueue) -> None:
    """A request can be escalated before a ticket is raised for it."""
    assert (await add(queue)).ticket_id is None


@pytest.mark.anyio
async def test_deleting_a_ticket_keeps_the_review_record(
    queue: ReviewQueue, session: AsyncSession
) -> None:
    """Losing the ticket must not erase that a human was asked to look."""
    ticket = await TicketService(session).create(
        customer_email="a@example.com", subject="s", body="b"
    )
    item = await add(queue, ticket_id=ticket.id)

    await session.execute(text("DELETE FROM tickets WHERE id=:i"), {"i": ticket.id})
    await session.commit()
    await session.refresh(item)

    assert item.ticket_id is None
    assert (await queue.get(item.id)).id == item.id


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pending_is_oldest_first(queue: ReviewQueue) -> None:
    """A queue is worked front to back."""
    first = await add(queue, "first")
    second = await add(queue, "second")

    assert [i.id for i in await queue.pending()] == [first.id, second.id]


@pytest.mark.anyio
async def test_unknown_item_raises_a_404(queue: ReviewQueue) -> None:
    with pytest.raises(ReviewItemNotFoundError) as excinfo:
        await queue.get(uuid.uuid4())

    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "review_item_not_found"


@pytest.mark.anyio
async def test_counts_report_every_status(queue: ReviewQueue) -> None:
    await add(queue)

    counts = await queue.counts()

    assert counts == {"pending": 1, "claimed": 0, "resolved": 0}


# --------------------------------------------------------------------------
# Claiming and resolving
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_claiming_takes_an_item_off_the_pending_list(queue: ReviewQueue) -> None:
    item = await add(queue)

    claimed = await queue.claim(item.id)

    assert claimed is not None
    assert claimed.status is ReviewStatus.CLAIMED
    assert await queue.pending() == []


@pytest.mark.anyio
async def test_an_item_cannot_be_claimed_twice(queue: ReviewQueue) -> None:
    """Two reviewers opening the queue together must not both take one item."""
    item = await add(queue)
    await queue.claim(item.id)

    assert await queue.claim(item.id) is None


@pytest.mark.anyio
async def test_claiming_something_that_does_not_exist_returns_none(
    queue: ReviewQueue,
) -> None:
    assert await queue.claim(uuid.uuid4()) is None


@pytest.mark.anyio
async def test_resolving_records_what_the_reviewer_decided(queue: ReviewQueue) -> None:
    item = await add(queue)
    await queue.claim(item.id)

    resolved = await queue.resolve(item.id, resolution="Refund issued manually.")

    assert resolved.status is ReviewStatus.RESOLVED
    assert resolved.resolution == "Refund issued manually."
    assert (await queue.counts())["resolved"] == 1


# --------------------------------------------------------------------------
# Integrity and safety
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_blank_message_is_rejected_by_the_database(
    session: AsyncSession,
) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO review_items (message, intent, reason) "
                "VALUES ('   ', 'billing', 'unresolved')"
            )
        )
    await session.rollback()


@pytest.mark.anyio
async def test_an_unknown_status_is_rejected(session: AsyncSession) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO review_items (message, intent, reason, status) "
                "VALUES ('m', 'billing', 'unresolved', 'banana')"
            )
        )
    await session.rollback()


@pytest.mark.anyio
async def test_logs_record_identifiers_not_customer_content(
    queue: ReviewQueue, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.review"):
        item = await add(queue, "my card 4242 was charged", proposed_reply="Refunded 4242.")

    assert str(item.id) in caplog.text
    assert "reason=unresolved" in caplog.text
    assert "4242" not in caplog.text
