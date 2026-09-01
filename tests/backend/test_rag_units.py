"""Embedding providers, chunking, and the provider registry. Offline."""

import math

import pytest

from app.core.config import Settings
from app.models import EMBEDDING_DIMENSIONS
from app.rag.chunking import DEFAULT_CHUNK_SIZE, MIN_CHUNK_SIZE, chunk_text
from app.rag.embeddings import (
    EmbeddingProvider,
    FastEmbedProvider,
    HashingEmbeddingProvider,
)
from app.rag.factory import PROVIDER_NAMES, build_embedding_provider

HASHING = HashingEmbeddingProvider()


# --------------------------------------------------------------------------
# Protocol / registry
# --------------------------------------------------------------------------


def test_providers_satisfy_the_protocol() -> None:
    assert isinstance(HASHING, EmbeddingProvider)
    assert isinstance(FastEmbedProvider(), EmbeddingProvider)


def test_registry_and_settings_options_agree() -> None:
    allowed = Settings.model_fields["embedding_provider"].annotation
    assert set(PROVIDER_NAMES) == set(allowed.__args__)


def test_factory_builds_the_configured_provider() -> None:
    built = build_embedding_provider(Settings(embedding_provider="hashing"))
    assert isinstance(built, HashingEmbeddingProvider)


def test_unknown_provider_is_rejected_by_settings() -> None:
    with pytest.raises(ValueError):
        Settings(embedding_provider="word2vec")


def test_fastembed_does_not_load_a_model_on_construction() -> None:
    """Importing or constructing must stay cheap; loading is lazy."""
    assert FastEmbedProvider()._model is None


# --------------------------------------------------------------------------
# Hashing provider
# --------------------------------------------------------------------------


def test_embedding_has_the_column_width() -> None:
    assert len(HASHING.embed_one("refund my order")) == EMBEDDING_DIMENSIONS


def test_embeddings_are_deterministic() -> None:
    assert HASHING.embed_one("same text") == HASHING.embed_one("same text")


def test_vectors_are_unit_length() -> None:
    """Cosine distance is only meaningful for normalised vectors."""
    norm = math.sqrt(sum(v * v for v in HASHING.embed_one("hello world")))
    assert norm == pytest.approx(1.0)


def test_empty_text_still_yields_a_usable_vector() -> None:
    """An all-zero vector has undefined cosine distance."""
    vector = HASHING.embed_one("   ")
    assert math.sqrt(sum(v * v for v in vector)) == pytest.approx(1.0)


def test_shared_tokens_rank_closer_than_unrelated_text() -> None:
    query = HASHING.embed_one("refund my order please")
    related = HASHING.embed_one("refund order")
    unrelated = HASHING.embed_one("configure smtp relay settings")

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, related) > cosine(query, unrelated)


def test_batch_preserves_order_and_matches_single() -> None:
    batch = HASHING.embed(["alpha", "beta"])

    assert batch[0] == HASHING.embed_one("alpha")
    assert batch[1] == HASHING.embed_one("beta")


def test_empty_batch() -> None:
    assert HASHING.embed([]) == []


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("A short paragraph.") == ["A short paragraph."]


def test_paragraphs_become_separate_chunks() -> None:
    assert chunk_text("First para.\n\nSecond para.") == ["First para.", "Second para."]


def test_blank_paragraphs_are_dropped() -> None:
    assert chunk_text("One.\n\n   \n\nTwo.") == ["One.", "Two."]


def test_oversized_paragraphs_are_split() -> None:
    paragraph = " ".join(["word"] * 500)
    chunks = chunk_text(paragraph, chunk_size=100)

    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks).split() == paragraph.split()  # nothing lost


def test_chunks_are_never_blank() -> None:
    assert all(c.strip() for c in chunk_text("a\n\n\n\nb"))


def test_whitespace_only_text_yields_nothing() -> None:
    assert chunk_text("   \n\n  ") == []


def test_chunk_size_floor_is_enforced() -> None:
    with pytest.raises(ValueError):
        chunk_text("text", chunk_size=MIN_CHUNK_SIZE - 1)


def test_default_chunk_size_is_sane() -> None:
    assert DEFAULT_CHUNK_SIZE >= MIN_CHUNK_SIZE
