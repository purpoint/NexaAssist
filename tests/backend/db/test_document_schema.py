"""Document schema and vector storage against real PostgreSQL + pgvector."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import EMBEDDING_DIMENSIONS

from .conftest import EXPECTED_DATABASE

pytestmark = pytest.mark.usefixtures("clean_tables")


def vec(*values: float) -> str:
    padded = list(values) + [0.0] * (EMBEDDING_DIMENSIONS - len(values))
    return "[" + ",".join(str(v) for v in padded) + "]"


async def add_document(engine: AsyncEngine, title: str = "Refunds") -> uuid.UUID:
    async with engine.begin() as connection:
        return await connection.scalar(
            text("INSERT INTO documents (title, content) VALUES (:t,:c) RETURNING id"),
            {"t": title, "c": "body text"},
        )


async def add_chunk(engine: AsyncEngine, doc: uuid.UUID, ordinal: int, embedding: str) -> uuid.UUID:
    async with engine.begin() as connection:
        return await connection.scalar(
            text(
                "INSERT INTO document_chunks (document_id, ordinal, content, embedding) "
                "VALUES (:d,:o,:c,CAST(:e AS vector)) RETURNING id"
            ),
            {"d": doc, "o": ordinal, "c": f"chunk {ordinal}", "e": embedding},
        )


@pytest.mark.anyio
async def test_targets_only_the_test_database(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        assert await c.scalar(text("SELECT current_database()")) == EXPECTED_DATABASE


@pytest.mark.anyio
async def test_vector_extension_is_installed(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        assert await c.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname='vector'")
        ) == 1


@pytest.mark.anyio
async def test_embedding_column_has_the_expected_width(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        atttypmod = await c.scalar(
            text(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid='document_chunks'::regclass AND attname='embedding'"
            )
        )

    assert atttypmod == EMBEDDING_DIMENSIONS


@pytest.mark.anyio
async def test_hnsw_cosine_index_exists(engine: AsyncEngine) -> None:
    async with engine.connect() as c:
        definition = await c.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_document_chunks_embedding'")
        )

    assert "hnsw" in definition and "vector_cosine_ops" in definition


@pytest.mark.anyio
async def test_chunk_round_trips(engine: AsyncEngine) -> None:
    doc = await add_document(engine)
    chunk = await add_chunk(engine, doc, 0, vec(1.0, 0.0, 0.0))

    async with engine.connect() as c:
        stored = await c.scalar(
            text("SELECT embedding FROM document_chunks WHERE id=:i"), {"i": chunk}
        )

    assert stored is not None


@pytest.mark.anyio
async def test_cosine_distance_orders_by_similarity(engine: AsyncEngine) -> None:
    """The point of the whole table: nearest first."""
    doc = await add_document(engine)
    await add_chunk(engine, doc, 0, vec(1.0, 0.0))
    await add_chunk(engine, doc, 1, vec(0.0, 1.0))

    async with engine.connect() as c:
        rows = await c.execute(
            text(
                "SELECT ordinal FROM document_chunks "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 2"
            ),
            {"q": vec(0.9, 0.1)},
        )

    assert [r[0] for r in rows] == [0, 1]


@pytest.mark.anyio
async def test_wrong_dimension_is_rejected(engine: AsyncEngine) -> None:
    """A mismatch fails loudly instead of producing meaningless distances."""
    doc = await add_document(engine)

    with pytest.raises(DBAPIError):
        await add_chunk(engine, doc, 0, "[1,2,3]")


@pytest.mark.anyio
async def test_deleting_a_document_removes_its_chunks(engine: AsyncEngine) -> None:
    doc = await add_document(engine)
    await add_chunk(engine, doc, 0, vec(1.0))

    async with engine.begin() as c:
        await c.execute(text("DELETE FROM documents WHERE id=:i"), {"i": doc})
    async with engine.connect() as c:
        remaining = await c.scalar(text("SELECT count(*) FROM document_chunks"))

    assert remaining == 0


@pytest.mark.anyio
async def test_ordinal_is_unique_within_a_document(engine: AsyncEngine) -> None:
    doc = await add_document(engine)
    await add_chunk(engine, doc, 0, vec(1.0))

    with pytest.raises(IntegrityError):
        await add_chunk(engine, doc, 0, vec(2.0))


@pytest.mark.anyio
async def test_same_ordinal_allowed_in_a_different_document(engine: AsyncEngine) -> None:
    first = await add_document(engine, "A")
    second = await add_document(engine, "B")
    await add_chunk(engine, first, 0, vec(1.0))

    assert await add_chunk(engine, second, 0, vec(1.0))


@pytest.mark.anyio
async def test_negative_ordinal_is_rejected(engine: AsyncEngine) -> None:
    doc = await add_document(engine)

    with pytest.raises(IntegrityError):
        await add_chunk(engine, doc, -1, vec(1.0))


@pytest.mark.anyio
async def test_blank_document_text_is_rejected(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        async with engine.begin() as c:
            await c.execute(
                text("INSERT INTO documents (title, content) VALUES ('  ','body')")
            )


@pytest.mark.anyio
async def test_chunk_requires_an_existing_document(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await add_chunk(engine, uuid.uuid4(), 0, vec(1.0))
