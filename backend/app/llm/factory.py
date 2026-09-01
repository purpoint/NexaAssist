"""Provider registry, and the dependency that hands one to a caller.

Adding a provider means one entry in ``_PROVIDERS`` and one option on
``Settings.llm_provider``; a test asserts those two stay in step.
"""

from collections.abc import Callable
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.llm.base import LLMConfig, LLMProvider
from app.llm.errors import LLMError
from app.llm.providers.groq_provider import GroqProvider
from app.llm.providers.static_provider import StaticLLMProvider
from app.schemas.intent import STATIC_EXAMPLE, IntentAnalysis

ProviderBuilder = Callable[[LLMConfig], LLMProvider]


def _build_static(config: LLMConfig) -> LLMProvider:
    """Wire the static provider's canned catalogue.

    The composition root supplies the domain responses, so
    ``static_provider.py`` itself stays free of domain vocabulary.
    """
    return StaticLLMProvider(config, canned={IntentAnalysis: STATIC_EXAMPLE})


_PROVIDERS: dict[str, ProviderBuilder] = {
    GroqProvider.name: GroqProvider,
    StaticLLMProvider.name: _build_static,
}

PROVIDER_NAMES: tuple[str, ...] = tuple(sorted(_PROVIDERS))


def config_from_settings(settings: Settings) -> LLMConfig:
    """Project the application settings onto the narrow provider config.

    This lives here rather than on ``Settings`` so that ``app.core`` keeps no
    dependency on ``app.llm`` -- the layering runs one way, core outwards.
    """
    return LLMConfig(
        provider=settings.llm_provider,
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        total_timeout_seconds=settings.llm_total_timeout_seconds,
        max_output_tokens=settings.llm_max_output_tokens,
        temperature=settings.llm_temperature,
    )


def build_provider(config: LLMConfig) -> LLMProvider:
    """Construct the provider named by ``config``."""
    try:
        builder = _PROVIDERS[config.provider]
    except KeyError:
        raise LLMError(
            f"Unknown LLM provider {config.provider!r}.",
            details={"provider": config.provider, "supported": list(PROVIDER_NAMES)},
        ) from None
    return builder(config)


@lru_cache(maxsize=1)
def _default_provider() -> LLMProvider:
    return build_provider(config_from_settings(get_settings()))


def get_llm_provider() -> LLMProvider:
    """FastAPI dependency returning the process-wide provider.

    Cached because constructing a provider builds an HTTP client with its own
    connection pool, and one per request would be wasteful. Tests either
    override this dependency or call ``build_provider`` with an explicit
    config.
    """
    return _default_provider()
