"""Embedding provider registry and dependency.

Adding a provider means one entry here and one option on
``Settings.embedding_provider``; a test asserts the two stay in step.
"""

from collections.abc import Callable
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.rag.embeddings import (
    EmbeddingProvider,
    FastEmbedProvider,
    HashingEmbeddingProvider,
)

_PROVIDERS: dict[str, Callable[[], EmbeddingProvider]] = {
    FastEmbedProvider.name: FastEmbedProvider,
    HashingEmbeddingProvider.name: HashingEmbeddingProvider,
}

PROVIDER_NAMES: tuple[str, ...] = tuple(sorted(_PROVIDERS))


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Construct the provider named by settings."""
    return _PROVIDERS[settings.embedding_provider]()


@lru_cache(maxsize=1)
def _default_provider() -> EmbeddingProvider:
    return build_embedding_provider(get_settings())


def get_embedding_provider() -> EmbeddingProvider:
    """FastAPI dependency: the process-wide embedding provider.

    Cached because loading an ONNX model on every request would dominate the
    latency of anything that touches it.
    """
    return _default_provider()
