"""ConversationService against real PostgreSQL."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import MessageRole
from app.services.conversation import ConversationService, estimate_tokens
from app.services.errors import ConversationNotFoundError
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
def service(session: AsyncSession) -> ConversationService:
    return ConversationService(session)


async def a_customer(session: AsyncSession) -> uuid.UUID:
    ticket = await TicketService(session).create(
        customer_email="ada@example.com", subject="s", body="b"
    )
    return ticket.customer_id


# --------------------------------------------------------------------------
# Starting and appending
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_start_creates_a_conversation(
    service: ConversationService, session: AsyncSession
) -> None:
    conversation = await service.start(await a_customer(session))

    assert isinstance(conversation.id, uuid.UUID)
    assert await service.get(conversation.id) is not None


@pytest.mark.anyio
async def test_positions_are_assigned_in_order(
    service: ConversationService, session: AsyncSession
) -> None:
    """Ordering must not depend on who appended last."""
    conversation = await service.start(await a_customer(session))

    first = await service.append(conversation.id, role=MessageRole.CUSTOMER, content="hi")
    second = await service.append(
        conversation.id, role=MessageRole.ASSISTANT, content="hello"
    )

    assert (first.position, second.position) == (0, 1)


@pytest.mark.anyio
async def test_appending_to_an_unknown_conversation_is_a_404(
    service: ConversationService,
) -> None:
    with pytest.raises(ConversationNotFoundError) as excinfo:
        await service.append(uuid.uuid4(), role=MessageRole.CUSTOMER, content="hi")

    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "conversation_not_found"


@pytest.mark.anyio
async def test_appends_are_committed(
    service: ConversationService, session: AsyncSession, test_database_url: str
) -> None:
    conversation = await service.start(await a_customer(session))
    await service.append(conversation.id, role=MessageRole.CUSTOMER, content="hi")

    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            stored = await c.scalar(
                text("SELECT count(*) FROM conversation_messages WHERE conversation_id=:i"),
                {"i": conversation.id},
            )
    finally:
        await engine.dispose()

    assert stored == 1


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_history_is_returned_in_order(
    service: ConversationService, session: AsyncSession
) -> None:
    conversation = await service.start(await a_customer(session))
    for index in range(4):
        await service.append(
            conversation.id, role=MessageRole.CUSTOMER, content=f"turn {index}"
        )

    history = await service.history(conversation.id)

    assert [m.position for m in history] == [0, 1, 2, 3]


@pytest.mark.anyio
async def test_a_limited_history_returns_the_most_recent_still_in_order(
    service: ConversationService, session: AsyncSession
) -> None:
    """Recency decides what is kept; reading order decides how it is returned."""
    conversation = await service.start(await a_customer(session))
    for index in range(5):
        await service.append(
            conversation.id, role=MessageRole.CUSTOMER, content=f"turn {index}"
        )

    recent = await service.history(conversation.id, limit=2)

    assert [m.position for m in recent] == [3, 4]


@pytest.mark.anyio
async def test_history_of_an_empty_conversation(
    service: ConversationService, session: AsyncSession
) -> None:
    conversation = await service.start(await a_customer(session))

    assert await service.history(conversation.id) == []


@pytest.mark.anyio
async def test_conversations_do_not_leak_into_one_another(
    service: ConversationService, session: AsyncSession
) -> None:
    customer = await a_customer(session)
    first = await service.start(customer)
    second = await service.start(customer)
    await service.append(first.id, role=MessageRole.CUSTOMER, content="only mine")

    assert await service.history(second.id) == []


# --------------------------------------------------------------------------
# Token estimate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text_in", "expected"), [("", 1), ("abcd", 1), ("abcde", 2), ("a" * 40, 10)]
)
def test_token_estimate_rounds_up_and_never_returns_zero(
    text_in: str, expected: int
) -> None:
    """Under-counting costs a failed request; over-counting costs a little context."""
    assert estimate_tokens(text_in) == expected


@pytest.mark.anyio
async def test_the_estimate_is_stored_with_the_message(
    service: ConversationService, session: AsyncSession
) -> None:
    conversation = await service.start(await a_customer(session))

    message = await service.append(
        conversation.id, role=MessageRole.CUSTOMER, content="a" * 40
    )

    assert message.token_estimate == 10


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_logs_record_shape_not_content(
    service: ConversationService, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    conversation = await service.start(await a_customer(session))

    with caplog.at_level(logging.INFO, logger="app.services.conversation"):
        await service.append(
            conversation.id, role=MessageRole.CUSTOMER, content="my card 4242 was charged"
        )

    assert "position=0" in caplog.text and "role=customer" in caplog.text
    assert "4242" not in caplog.text
