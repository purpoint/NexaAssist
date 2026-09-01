"""TicketService against a real PostgreSQL instance."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Customer, Ticket, TicketStatus
from app.services.errors import TicketNotFoundError
from app.services.ticket import MAX_PAGE_SIZE, TicketService, normalise_email

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        assert await opened.scalar(text("SELECT current_database()")) == (
            EXPECTED_DATABASE
        )
        yield opened
    await engine.dispose()


@pytest.fixture
def service(session: AsyncSession) -> TicketService:
    return TicketService(session)


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_persists_ticket_and_customer(
    service: TicketService, session: AsyncSession
) -> None:
    ticket = await service.create(
        customer_email="ada@example.com", subject="Charged twice", body="Please refund."
    )

    assert isinstance(ticket.id, uuid.UUID)
    assert ticket.status is TicketStatus.OPEN
    assert ticket.created_at is not None

    # Visible to a different session: the service committed, it did not merely
    # flush into its own transaction.
    assert await _ticket_exists(session.bind.url.render_as_string(hide_password=False), ticket.id)


@pytest.mark.anyio
async def test_create_normalises_the_email(service: TicketService, session: AsyncSession) -> None:
    await service.create(
        customer_email="  Ada@EXAMPLE.com  ", subject="s", body="b"
    )

    stored = await session.scalar(select(Customer.email))
    assert stored == "ada@example.com"


@pytest.mark.anyio
async def test_second_ticket_reuses_the_existing_customer(
    service: TicketService, session: AsyncSession
) -> None:
    first = await service.create(customer_email="ada@example.com", subject="a", body="b")
    second = await service.create(customer_email="ada@example.com", subject="c", body="d")

    assert first.customer_id == second.customer_id
    assert len((await session.scalars(select(Customer))).all()) == 1


@pytest.mark.anyio
async def test_concurrent_first_contact_creates_one_customer(
    test_database_url: str,
) -> None:
    """Both callers see no customer and both insert; the unique constraint
    rejects one, and the savepoint keeps the loser's ticket work intact."""
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def create_one(subject: str) -> Ticket:
        async with factory() as opened:
            return await TicketService(opened).create(
                customer_email="race@example.com", subject=subject, body="body"
            )

    try:
        first, second = await asyncio.gather(create_one("one"), create_one("two"))
        async with factory() as opened:
            customers = (await opened.scalars(select(Customer))).all()
            tickets = (await opened.scalars(select(Ticket))).all()
    finally:
        await engine.dispose()

    assert len(customers) == 1
    assert len(tickets) == 2
    assert first.customer_id == second.customer_id == customers[0].id


@pytest.mark.anyio
async def test_create_rejects_text_the_database_forbids(service: TicketService) -> None:
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await service.create(customer_email="ada@example.com", subject="   ", body="b")


# --------------------------------------------------------------------------
# get
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_returns_the_ticket(service: TicketService) -> None:
    created = await service.create(customer_email="a@example.com", subject="s", body="b")

    assert (await service.get(created.id)).id == created.id


@pytest.mark.anyio
async def test_get_raises_for_an_unknown_id(service: TicketService) -> None:
    missing = uuid.uuid4()

    with pytest.raises(TicketNotFoundError) as excinfo:
        await service.get(missing)

    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "ticket_not_found"
    assert excinfo.value.details == {"ticket_id": str(missing)}


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_returns_newest_first(service: TicketService) -> None:
    for subject in ("first", "second", "third"):
        await service.create(customer_email="a@example.com", subject=subject, body="b")

    subjects = [t.subject for t in await service.list()]

    assert subjects[0] == "third"
    assert set(subjects) == {"first", "second", "third"}


@pytest.mark.anyio
async def test_list_filters_by_status(
    service: TicketService, session: AsyncSession
) -> None:
    keep = await service.create(customer_email="a@example.com", subject="open", body="b")
    other = await service.create(customer_email="a@example.com", subject="done", body="b")
    other.status = TicketStatus.RESOLVED
    await session.commit()

    open_only = await service.list(status=TicketStatus.OPEN)

    assert [t.id for t in open_only] == [keep.id]
    assert [t.id for t in await service.list(status=TicketStatus.RESOLVED)] == [other.id]


@pytest.mark.anyio
async def test_list_paginates_without_skipping_or_repeating(
    service: TicketService,
) -> None:
    """Rows created inside one second share a timestamp; id breaks the tie."""
    for index in range(6):
        await service.create(
            customer_email="a@example.com", subject=f"s{index}", body="b"
        )

    page_one = await service.list(limit=3, offset=0)
    page_two = await service.list(limit=3, offset=3)
    ids = [t.id for t in page_one] + [t.id for t in page_two]

    assert len(ids) == 6
    assert len(set(ids)) == 6


@pytest.mark.anyio
async def test_list_caps_the_page_size(service: TicketService) -> None:
    for index in range(3):
        await service.create(
            customer_email="a@example.com", subject=f"s{index}", body="b"
        )

    assert len(await service.list(limit=MAX_PAGE_SIZE * 10)) == 3


@pytest.mark.anyio
async def test_list_is_empty_when_nothing_exists(service: TicketService) -> None:
    assert await service.list() == []


# --------------------------------------------------------------------------
# Logging policy
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_service_logs_identifiers_but_never_customer_content(
    service: TicketService, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.ticket"):
        ticket = await service.create(
            customer_email="private@example.com",
            subject="Card 4242 double charged",
            body="Call me on 555-0100",
        )

    text_logged = caplog.text
    assert str(ticket.id) in text_logged
    assert "status=open" in text_logged
    for secret in ("private@example.com", "4242", "555-0100"):
        assert secret not in text_logged


def test_normalise_email() -> None:
    assert normalise_email("  Ada@Example.COM ") == "ada@example.com"


# --------------------------------------------------------------------------
# Helper
# --------------------------------------------------------------------------


async def _ticket_exists(url: str, ticket_id: uuid.UUID) -> bool:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            found = await connection.scalar(
                text("SELECT count(*) FROM tickets WHERE id = :i"), {"i": ticket_id}
            )
    finally:
        await engine.dispose()
    return bool(found)
