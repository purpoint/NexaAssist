"""DocumentService against real PostgreSQL + pgvector."""

import logging
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Document, DocumentChunk
from app.rag.embeddings import HashingEmbeddingProvider
from app.services.document import DocumentService
from app.services.errors import DocumentNotFoundError

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")

REFUNDS = "Refunds are issued within 5 business days.\n\nContact billing support to start a refund."
PASSWORDS = "To reset your password use the forgot password link.\n\nReset links expire after one hour."


@pytest.fixture
async def session(test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        assert await opened.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE
        yield opened
    await engine.dispose()


@pytest.fixture
def service(session: AsyncSession) -> DocumentService:
    # The deterministic embedder: no model download, no network.
    return DocumentService(session, HashingEmbeddingProvider())


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ingest_stores_document_and_chunks(
    service: DocumentService, session: AsyncSession
) -> None:
    document = await service.ingest(title="Refunds", content=REFUNDS)

    chunks = (
        await session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
    ).all()

    assert isinstance(document.id, uuid.UUID)
    assert len(chunks) == 2
    assert sorted(c.ordinal for c in chunks) == [0, 1]


@pytest.mark.anyio
async def test_ingest_embeds_every_chunk(
    service: DocumentService, session: AsyncSession
) -> None:
    await service.ingest(title="Refunds", content=REFUNDS)

    chunk = await session.scalar(select(DocumentChunk))

    assert chunk.embedding is not None
    assert len(chunk.embedding) == 384


@pytest.mark.anyio
async def test_ingest_commits(service: DocumentService, test_database_url: str) -> None:
    document = await service.ingest(title="Refunds", content=REFUNDS)

    engine = create_async_engine(test_database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            found = await connection.scalar(
                text("SELECT count(*) FROM documents WHERE id=:i"), {"i": document.id}
            )
    finally:
        await engine.dispose()

    assert found == 1


@pytest.mark.anyio
async def test_ingest_is_atomic_across_document_and_chunks(
    service: DocumentService, session: AsyncSession
) -> None:
    """A document without its chunks would be silently unretrievable."""
    await service.ingest(title="Refunds", content=REFUNDS)

    documents = len((await session.scalars(select(Document))).all())
    chunks = len((await session.scalars(select(DocumentChunk))).all())

    assert documents == 1 and chunks == 2


# --------------------------------------------------------------------------
# get / list
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_returns_the_document(service: DocumentService) -> None:
    created = await service.ingest(title="Refunds", content=REFUNDS)

    assert (await service.get(created.id)).title == "Refunds"


@pytest.mark.anyio
async def test_get_raises_for_unknown_id(service: DocumentService) -> None:
    missing = uuid.uuid4()

    with pytest.raises(DocumentNotFoundError) as excinfo:
        await service.get(missing)

    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "document_not_found"


@pytest.mark.anyio
async def test_list_returns_newest_first(service: DocumentService) -> None:
    await service.ingest(title="First", content=REFUNDS)
    await service.ingest(title="Second", content=PASSWORDS)

    assert [d.title for d in await service.list()][0] == "Second"


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_search_returns_the_relevant_document_first(
    service: DocumentService,
) -> None:
    await service.ingest(title="Refunds", content=REFUNDS)
    await service.ingest(title="Passwords", content=PASSWORDS)

    hits = await service.search("refund", top_k=4)

    assert hits
    assert hits[0].document_title == "Refunds"


@pytest.mark.anyio
async def test_search_orders_by_increasing_distance(service: DocumentService) -> None:
    await service.ingest(title="Refunds", content=REFUNDS)
    await service.ingest(title="Passwords", content=PASSWORDS)

    distances = [h.distance for h in await service.search("password reset", top_k=4)]

    assert distances == sorted(distances)


@pytest.mark.anyio
async def test_search_respects_top_k(service: DocumentService) -> None:
    await service.ingest(title="Refunds", content=REFUNDS)
    await service.ingest(title="Passwords", content=PASSWORDS)

    assert len(await service.search("anything", top_k=1)) == 1


@pytest.mark.anyio
async def test_search_on_an_empty_corpus(service: DocumentService) -> None:
    assert await service.search("refund") == []


@pytest.mark.anyio
async def test_hits_carry_provenance_for_citation(service: DocumentService) -> None:
    document = await service.ingest(title="Refunds", content=REFUNDS)

    hit = (await service.search("refund", top_k=1))[0]

    assert hit.document_id == document.id
    assert hit.document_title == "Refunds"
    assert hit.ordinal in (0, 1)
    assert hit.content
    assert 0.0 <= hit.similarity <= 1.0


@pytest.mark.anyio
async def test_deleting_a_document_removes_it_from_retrieval(
    service: DocumentService, session: AsyncSession
) -> None:
    document = await service.ingest(title="Refunds", content=REFUNDS)
    await session.execute(
        text("DELETE FROM documents WHERE id=:i"), {"i": document.id}
    )
    await session.commit()

    assert await service.search("refund") == []


# --------------------------------------------------------------------------
# Logging policy
# --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_service_logs_counts_not_document_content(
    service: DocumentService, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "Customer 4242 asked about invoice 99887"

    with caplog.at_level(logging.INFO, logger="app.services.document"):
        document = await service.ingest(title="Refunds", content=secret)

    assert str(document.id) in caplog.text
    assert "chunks=1" in caplog.text
    assert "4242" not in caplog.text
    assert "99887" not in caplog.text
