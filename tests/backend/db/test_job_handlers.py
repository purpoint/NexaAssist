"""Background handlers end to end, against real PostgreSQL.

The point of these is that the queued path and the request path produce the
same rows. A handler that quietly did something slightly different would pass
every offline test in the suite.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.jobs.factory import build_handler_registry
from app.jobs.memory import InMemoryJobQueue
from app.jobs.worker import JobOutcome, JobWorker
from app.models import Customer, Document, DocumentChunk, Ticket
from app.rag.embeddings import HashingEmbeddingProvider
from app.services.document import DocumentService
from app.services.ticket import TicketService

from .conftest import EXPECTED_DATABASE

pytestmark = [pytest.mark.usefixtures("clean_tables"), pytest.mark.anyio]

ARTICLE = "Refunds take 5 business days.\n\nContact billing to start one."


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        assert await opened.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE
        yield opened
    await engine.dispose()


@pytest.fixture
def queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


@pytest.fixture
def worker(queue: InMemoryJobQueue, session: AsyncSession) -> JobWorker:
    registry = build_handler_registry(
        documents=DocumentService(session, HashingEmbeddingProvider()),
        tickets=TicketService(session),
    )
    return JobWorker(queue, registry)


async def test_the_shipped_handlers_are_registered(session: AsyncSession) -> None:
    registry = build_handler_registry(
        documents=DocumentService(session, HashingEmbeddingProvider()),
        tickets=TicketService(session),
    )
    assert registry.names() == ["create_ticket", "ingest_document"]


async def test_a_queued_document_is_ingested_with_its_chunks(
    worker: JobWorker, queue: InMemoryJobQueue, session: AsyncSession
) -> None:
    await queue.enqueue("ingest_document", {"title": "Refunds", "content": ARTICLE})

    run = await worker.run_once()
    assert run.outcome is JobOutcome.SUCCEEDED

    document = (await session.scalars(select(Document))).one()
    assert document.title == "Refunds"
    chunks = await session.scalar(
        select(func.count()).select_from(DocumentChunk).where(
            DocumentChunk.document_id == document.id
        )
    )
    assert chunks == 2


async def test_a_queued_ticket_creates_the_customer_too(
    worker: JobWorker, queue: InMemoryJobQueue, session: AsyncSession
) -> None:
    await queue.enqueue(
        "create_ticket",
        {
            "customer_email": "person@example.com",
            "subject": "Where is my refund?",
            "body": "It has been two weeks.",
        },
    )

    run = await worker.run_once()
    assert run.outcome is JobOutcome.SUCCEEDED

    ticket = (await session.scalars(select(Ticket))).one()
    customer = (await session.scalars(select(Customer))).one()
    assert ticket.subject == "Where is my refund?"
    assert ticket.customer_id == customer.id
    assert customer.email == "person@example.com"


async def test_the_queued_path_matches_the_direct_path(
    worker: JobWorker, queue: InMemoryJobQueue, session: AsyncSession
) -> None:
    """Same service, same rows -- the handler adds nothing of its own."""
    await DocumentService(session, HashingEmbeddingProvider()).ingest(
        title="Direct", content=ARTICLE
    )
    await queue.enqueue("ingest_document", {"title": "Queued", "content": ARTICLE})
    await worker.run_once()

    documents = {d.title: d for d in (await session.scalars(select(Document))).all()}
    assert set(documents) == {"Direct", "Queued"}
    counts = {
        title: await session.scalar(
            select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.document_id == document.id
            )
        )
        for title, document in documents.items()
    }
    assert counts["Direct"] == counts["Queued"]


async def test_a_batch_drains_in_order(
    worker: JobWorker, queue: InMemoryJobQueue, session: AsyncSession
) -> None:
    for index in range(3):
        await queue.enqueue(
            "ingest_document", {"title": f"Doc {index}", "content": ARTICLE}
        )

    runs = await worker.drain()
    assert [run.outcome for run in runs] == [JobOutcome.SUCCEEDED] * 3
    titles = [d.title for d in (await session.scalars(select(Document))).all()]
    assert sorted(titles) == ["Doc 0", "Doc 1", "Doc 2"]


async def test_an_invalid_payload_writes_nothing(
    worker: JobWorker, queue: InMemoryJobQueue, session: AsyncSession
) -> None:
    await queue.enqueue("ingest_document", {"title": "", "content": ARTICLE})

    run = await worker.run_once()
    assert run.outcome is JobOutcome.DEAD_LETTERED
    assert await session.scalar(select(func.count()).select_from(Document)) == 0
