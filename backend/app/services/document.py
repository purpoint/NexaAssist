"""Document ingestion and retrieval.

Depends on an ``AsyncSession`` and the ``EmbeddingProvider`` protocol -- no
FastAPI, no vendor SDK. The service owns the transaction boundary: ingestion is
complete only when the document and every one of its chunks exists.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Document, DocumentChunk
from app.rag.chunking import chunk_text
from app.rag.embeddings import EmbeddingProvider
from app.services.errors import DocumentNotFoundError

logger = get_logger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """One search hit, with enough provenance to cite it."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    ordinal: int
    content: str
    distance: float

    @property
    def similarity(self) -> float:
        """Cosine similarity, i.e. 1 - distance, clamped to [0, 1]."""
        return max(0.0, min(1.0, 1.0 - self.distance))


class DocumentService:
    """Ingests documents and retrieves the chunks nearest a query."""

    def __init__(self, session: AsyncSession, embedder: EmbeddingProvider) -> None:
        self._session = session
        self._embedder = embedder

    async def ingest(self, *, title: str, content: str) -> Document:
        """Store a document and its embedded chunks in one transaction."""
        pieces = chunk_text(content)
        vectors = self._embedder.embed(pieces)

        document = Document(title=title, content=content)
        self._session.add(document)
        await self._session.flush()

        self._session.add_all(
            [
                DocumentChunk(
                    document_id=document.id,
                    ordinal=ordinal,
                    content=piece,
                    embedding=vector,
                )
                for ordinal, (piece, vector) in enumerate(zip(pieces, vectors, strict=True))
            ]
        )
        await self._session.commit()

        # Identifiers and counts only: document content is customer material.
        logger.info(
            "document ingested document_id=%s chunks=%d embedder=%s",
            document.id,
            len(pieces),
            self._embedder.name,
        )
        return document

    async def get(self, document_id: uuid.UUID) -> Document:
        document = await self._session.get(Document, document_id)
        if document is None:
            raise DocumentNotFoundError(details={"document_id": str(document_id)})
        return document

    async def list(self, *, limit: int = 20, offset: int = 0) -> Sequence[Document]:
        statement = (
            select(Document)
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._session.scalars(statement)).all()

    async def search(
        self, query: str, *, top_k: int = 4
    ) -> Sequence[RetrievedChunk]:
        """Return the chunks nearest ``query``, nearest first.

        Ranking happens in PostgreSQL with the cosine operator, so the HNSW
        index can serve it; pulling rows into Python to sort would defeat the
        index entirely.
        """
        vector = self._embedder.embed_one(query)
        distance = DocumentChunk.embedding.cosine_distance(vector)

        statement = (
            select(
                DocumentChunk.id,
                DocumentChunk.document_id,
                Document.title,
                DocumentChunk.ordinal,
                DocumentChunk.content,
                distance.label("distance"),
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .order_by(distance)
            .limit(top_k)
        )
        rows = (await self._session.execute(statement)).all()
        # NOTE: ``list`` is shadowed by the method of that name on this class,
        # so annotations here use Sequence.

        logger.info("retrieval hits=%d top_k=%d embedder=%s", len(rows), top_k, self._embedder.name)
        return [
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                document_title=row.title,
                ordinal=row.ordinal,
                content=row.content,
                distance=float(row.distance),
            )
            for row in rows
        ]
