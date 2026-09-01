"""Knowledge-base documents and the chunks retrieval runs against.

A document is what a human ingests; a chunk is what a similarity search
matches. They are separate tables because retrieval works at chunk granularity
while provenance -- the citation a reader follows -- belongs to the document.
"""

import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    pass

EMBEDDING_DIMENSIONS = 384
"""Fixed by the embedding model (BAAI/bge-small-en-v1.5).

The column width is part of the schema, so changing models is a migration, not
a setting. A dimension mismatch is rejected by PostgreSQL rather than silently
producing meaningless distances.
"""


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One ingested source document."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("length(btrim(content)) > 0", name="content_not_blank"),
        Index("ix_documents_created_at", "created_at"),
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        lazy="raise",
        # Deleting a document deletes its chunks: a chunk has no meaning
        # without the document it was cut from. Contrast tickets, which stay
        # meaningful beyond the customer row and so use RESTRICT.
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A retrievable span of a document, with its embedding."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("length(btrim(content)) > 0", name="content_not_blank"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_document_id_ordinal"),
        Index("ix_document_chunks_document_id", "document_id"),
        # HNSW with cosine distance. The embedding model returns L2-normalised
        # vectors, so cosine is the matching operator class; using the default
        # L2 class here would rank by a distance the vectors were not built for.
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=False
    )

    document: Mapped[Document] = relationship(back_populates="chunks", lazy="raise")
