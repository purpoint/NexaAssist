"""Document and chunk models, offline."""

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Integer, String, Text

from app.models import EMBEDDING_DIMENSIONS, Document, DocumentChunk

DOCUMENTS = Document.__table__
CHUNKS = DocumentChunk.__table__


def test_tables_are_registered() -> None:
    assert DOCUMENTS.name == "documents"
    assert CHUNKS.name == "document_chunks"


def test_document_columns() -> None:
    assert set(DOCUMENTS.columns.keys()) == {"id", "title", "content", "created_at", "updated_at"}
    assert isinstance(DOCUMENTS.columns["title"].type, String)
    assert DOCUMENTS.columns["title"].type.length == 300
    assert isinstance(DOCUMENTS.columns["content"].type, Text)


def test_chunk_columns() -> None:
    assert set(CHUNKS.columns.keys()) == {
        "id", "document_id", "ordinal", "content", "embedding", "created_at", "updated_at",
    }
    assert isinstance(CHUNKS.columns["ordinal"].type, Integer)


def test_embedding_column_width_matches_the_model() -> None:
    """The width is schema, not configuration: changing models is a migration."""
    column = CHUNKS.columns["embedding"]

    assert isinstance(column.type, Vector)
    assert column.type.dim == EMBEDDING_DIMENSIONS == 384
    assert column.nullable is False


def test_chunks_cascade_with_their_document() -> None:
    """A chunk has no meaning without the document it was cut from.

    Contrast tickets -> customers, which is RESTRICT: a ticket stays meaningful
    beyond the customer row.
    """
    fk = next(iter(CHUNKS.columns["document_id"].foreign_keys))

    assert fk.ondelete == "CASCADE"
    assert fk.constraint.name == "fk_document_chunks_document_id_documents"


def test_chunk_ordinals_are_unique_within_a_document() -> None:
    assert any(
        c.name == "uq_document_chunks_document_id_ordinal" for c in CHUNKS.constraints
    )


def test_check_constraints() -> None:
    names = {c.name for c in CHUNKS.constraints if c.name and c.name.startswith("ck_")}

    assert "ck_document_chunks_content_not_blank" in names
    assert "ck_document_chunks_ordinal_non_negative" in names


def test_vector_index_uses_cosine_operator_class() -> None:
    """The model returns L2-normalised vectors; L2 ranking would be wrong."""
    index = next(i for i in CHUNKS.indexes if i.name == "ix_document_chunks_embedding")

    assert index.dialect_options["postgresql"]["using"] == "hnsw"
    assert index.dialect_options["postgresql"]["ops"] == {"embedding": "vector_cosine_ops"}


@pytest.mark.parametrize(
    ("model", "attribute"), [(Document, "chunks"), (DocumentChunk, "document")]
)
def test_relationships_refuse_to_lazy_load(model: type, attribute: str) -> None:
    assert model.__mapper__.relationships[attribute].lazy == "raise"
