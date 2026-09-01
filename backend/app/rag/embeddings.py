"""Embedding providers.

Mirrors the LLM layer deliberately: a ``Protocol`` plus a real implementation
and a deterministic offline one. The application depends on the protocol, so
swapping models is an adapter rather than a refactor.

The vector width is *not* configurable. It is the width of a database column,
so changing the model is a migration -- see ``app.models.document``.
"""

import hashlib
import math
from typing import Protocol, runtime_checkable

from app.models.document import EMBEDDING_DIMENSIONS

FASTEMBED_MODEL = "BAAI/bge-small-en-v1.5"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors of :data:`EMBEDDING_DIMENSIONS` floats."""

    name: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch. Order of the result matches the input."""
        ...

    def embed_one(self, text: str) -> list[float]:
        """Embed a single string."""
        ...


class FastEmbedProvider:
    """Local ONNX embeddings; no API key and no network after first use.

    The model is loaded lazily: importing this module must stay cheap, and a
    process that never embeds should not pay to initialise one.
    """

    name = "fastembed"

    def __init__(self, model_name: str = FASTEMBED_MODEL) -> None:
        self._model_name = model_name
        self._model: object | None = None

    def _loaded(self) -> object:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [list(map(float, v)) for v in self._loaded().embed(texts)]
        for vector in vectors:
            if len(vector) != EMBEDDING_DIMENSIONS:  # pragma: no cover - guard
                raise ValueError(
                    f"{self._model_name} returned {len(vector)} dimensions, "
                    f"expected {EMBEDDING_DIMENSIONS}"
                )
        return vectors

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class HashingEmbeddingProvider:
    """Deterministic embeddings derived from token hashes.

    Not a semantic model, and not pretending to be one. It exists so the suite
    can exercise storage, retrieval, ranking, and the API without downloading a
    model or reaching the network, and so the application starts without one.

    Identical text always yields an identical vector, and texts sharing tokens
    land closer together than texts that share none -- enough structure for
    ordering assertions to mean something.
    """

    name = "hashing"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for token in _tokenise(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
            # Sign from a different byte so tokens do not all push one way.
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        return _normalise(vector)


def _tokenise(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]


def _normalise(vector: list[float]) -> list[float]:
    """Scale to unit length so cosine distance behaves as expected."""
    magnitude = math.sqrt(sum(v * v for v in vector))
    if magnitude == 0.0:
        # An all-zero vector has undefined cosine distance; anchor it instead.
        vector = [0.0] * EMBEDDING_DIMENSIONS
        vector[0] = 1.0
        return vector
    return [v / magnitude for v in vector]
