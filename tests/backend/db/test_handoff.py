"""Handoff to a human, over the real review queue."""

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.escalation.criteria import EscalationReason
from app.escalation.factory import build_handoff
from app.escalation.handoff import HANDOFF_NOTICE, HandoffService
from app.models import ReviewStatus
from app.routing.router import RouteReason, RoutedReply, RoutingDecision
from app.schemas.intent import IntentCategory
from app.services.review import ReviewQueue

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
def handoff(session: AsyncSession) -> HandoffService:
    return build_handoff(session=session, settings=Settings())


def routed(
    *,
    reply: str = "Your ticket is open.",
    handled: bool = True,
    intent: IntentCategory = IntentCategory.BILLING,
    confidence: float = 0.9,
    policy_modified: bool = False,
) -> RoutedReply:
    return RoutedReply(
        decision=RoutingDecision(
            intent=intent,
            confidence=confidence,
            handler="agent",
            reason=RouteReason.MATCHED,
            fallback=False,
        ),
        reply=reply,
        handled=handled,
        policy_modified=policy_modified,
        policy_rule="no_financial_commitments" if policy_modified else None,
    )


# --------------------------------------------------------------------------
# No escalation
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_clean_resolved_reply_is_not_escalated(
    handoff: HandoffService, session: AsyncSession
) -> None:
    result = await handoff.consider("q", routed())

    assert result.escalated is False
    assert result.reply == "Your ticket is open."
    assert await ReviewQueue(session).pending() == []


# --------------------------------------------------------------------------
# Escalation
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_unresolved_request_is_queued(
    handoff: HandoffService, session: AsyncSession
) -> None:
    result = await handoff.consider("why was I charged", routed(handled=False))

    assert result.escalated is True
    assert result.queued is True
    assert EscalationReason.UNRESOLVED in result.reasons

    pending = await ReviewQueue(session).pending()
    assert len(pending) == 1
    assert pending[0].status is ReviewStatus.PENDING
    assert pending[0].message == "why was I charged"


@pytest.mark.anyio
async def test_the_queued_item_records_what_would_have_been_sent(
    handoff: HandoffService, session: AsyncSession
) -> None:
    """A reviewer needs to see what nearly went out."""
    await handoff.consider("q", routed(reply="I have refunded you.", handled=False))

    assert (await ReviewQueue(session).pending())[0].proposed_reply == "I have refunded you."


@pytest.mark.anyio
async def test_a_policy_modification_escalates(handoff: HandoffService) -> None:
    result = await handoff.consider("q", routed(policy_modified=True))

    assert EscalationReason.POLICY in result.reasons


@pytest.mark.anyio
async def test_a_complaint_escalates(handoff: HandoffService) -> None:
    result = await handoff.consider("q", routed(intent=IntentCategory.COMPLAINT))

    assert EscalationReason.COMPLAINT in result.reasons


@pytest.mark.anyio
async def test_an_item_can_be_linked_to_a_ticket(
    handoff: HandoffService, session: AsyncSession
) -> None:
    from app.services.ticket import TicketService

    ticket = await TicketService(session).create(
        customer_email="a@example.com", subject="s", body="b"
    )

    await handoff.consider("q", routed(handled=False), ticket_id=ticket.id)

    assert (await ReviewQueue(session).pending())[0].ticket_id == ticket.id


# --------------------------------------------------------------------------
# What the customer sees
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_customer_is_told_a_person_is_involved(
    handoff: HandoffService,
) -> None:
    result = await handoff.consider("q", routed(handled=False))

    assert result.reply.endswith(HANDOFF_NOTICE)


@pytest.mark.anyio
async def test_the_notice_is_not_repeated(handoff: HandoffService) -> None:
    """Policy replies already mention an agent; saying it twice reads badly."""
    result = await handoff.consider(
        "q", routed(reply="I have passed this to a support agent.", handled=False)
    )

    assert result.reply.lower().count("support agent") == 1


# --------------------------------------------------------------------------
# Failure handling
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_queue_failure_does_not_cost_the_customer_their_reply(
    session: AsyncSession,
) -> None:
    """Losing the queue entry is recoverable; dropping the reply is not."""
    from sqlalchemy.exc import OperationalError

    class BrokenQueue:
        async def enqueue(self, **kw: Any) -> Any:
            raise OperationalError("INSERT", {}, Exception("db down"))

    from app.escalation.criteria import EscalationCriteria

    service = HandoffService(BrokenQueue(), EscalationCriteria())

    result = await service.consider("q", routed(handled=False))

    assert result.escalated is True
    assert result.queued is False
    assert result.review_id is None
    assert result.reply.endswith(HANDOFF_NOTICE)


@pytest.mark.anyio
async def test_a_queue_failure_logs_only_the_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from sqlalchemy.exc import OperationalError

    from app.escalation.criteria import EscalationCriteria

    class BrokenQueue:
        async def enqueue(self, **kw: Any) -> Any:
            raise OperationalError("INSERT INTO review_items", {}, Exception("host=db"))

    with caplog.at_level(logging.WARNING, logger="app.escalation.handoff"):
        await HandoffService(BrokenQueue(), EscalationCriteria()).consider(
            "q", routed(handled=False)
        )

    assert "OperationalError" in caplog.text
    assert "host=db" not in caplog.text
    assert "INSERT INTO" not in caplog.text


@pytest.mark.anyio
async def test_the_result_leaks_no_internals(handoff: HandoffService) -> None:
    result = await handoff.consider(
        "card 4242 charged twice", routed(reply="Looking into it.", handled=False)
    )

    rendered = str(result.model_dump())
    for leak in ("postgresql", "asyncpg", "Traceback", "4242"):
        assert leak not in rendered
    assert isinstance(result.review_id, uuid.UUID)
