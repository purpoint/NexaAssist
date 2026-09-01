"""Context windows built from real stored history."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import MessageRole
from app.services.context import ContextWindow
from app.services.conversation import ConversationService
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


async def exchange(session: AsyncSession, turns: int) -> tuple[ConversationService, object]:
    service = ConversationService(session)
    ticket = await TicketService(session).create(
        customer_email="ada@example.com", subject="s", body="b"
    )
    conversation = await service.start(ticket.customer_id)
    for index in range(turns):
        await service.append(
            conversation.id,
            role=MessageRole.CUSTOMER if index % 2 == 0 else MessageRole.ASSISTANT,
            content=f"turn {index} " + "detail " * 10,
        )
    return service, conversation


@pytest.mark.anyio
async def test_a_short_exchange_fits_entirely(session: AsyncSession) -> None:
    service, conversation = await exchange(session, 3)

    context = ContextWindow(max_tokens=2_000).build(
        await service.history(conversation.id)
    )

    assert len(context.messages) == 3
    assert context.dropped == 0


@pytest.mark.anyio
async def test_a_long_exchange_keeps_the_most_recent_turns(
    session: AsyncSession,
) -> None:
    service, conversation = await exchange(session, 12)
    stored = await service.history(conversation.id)

    context = ContextWindow(max_tokens=60).build(stored)

    assert context.dropped > 0
    assert context.total_tokens <= 60
    # The last stored turn must be present: it carries the current request.
    assert context.messages[-1].content == stored[-1].content


@pytest.mark.anyio
async def test_roles_survive_the_round_trip(session: AsyncSession) -> None:
    service, conversation = await exchange(session, 2)

    context = ContextWindow(max_tokens=2_000).build(
        await service.history(conversation.id)
    )

    assert [m.role for m in context.messages] == [
        MessageRole.CUSTOMER,
        MessageRole.ASSISTANT,
    ]
    assert "customer:" in context.render() and "assistant:" in context.render()


@pytest.mark.anyio
async def test_stored_estimates_are_what_the_window_budgets_against(
    session: AsyncSession,
) -> None:
    """The window must not re-tokenise; it trusts the stored estimate."""
    service, conversation = await exchange(session, 4)
    stored = await service.history(conversation.id)

    context = ContextWindow(max_tokens=2_000).build(stored)

    assert context.total_tokens == sum(m.token_estimate for m in stored)


@pytest.mark.anyio
async def test_an_empty_conversation_yields_an_empty_window(
    session: AsyncSession,
) -> None:
    service = ConversationService(session)
    ticket = await TicketService(session).create(
        customer_email="a@example.com", subject="s", body="b"
    )
    conversation = await service.start(ticket.customer_id)

    context = ContextWindow().build(await service.history(conversation.id))

    assert context.messages == []
