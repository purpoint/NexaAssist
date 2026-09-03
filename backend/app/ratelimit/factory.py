"""Rate limiter registry and dependency.

Adding a backend means one entry here and one option on
``Settings.rate_limit_provider``; a test asserts the two stay in step.
"""

from collections.abc import Callable
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.ratelimit.base import RateLimiter
from app.ratelimit.errors import RateLimitConfigurationError
from app.ratelimit.limiters import InMemoryRateLimiter, NullRateLimiter


def _build_memory(settings: Settings) -> RateLimiter:
    return InMemoryRateLimiter(
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )


def _build_redis(settings: Settings) -> RateLimiter:
    if settings.redis_url is None:
        raise RateLimitConfigurationError(
            "The redis rate limiter requires REDIS_URL.",
        )
    from app.ratelimit.redis_limiter import RedisRateLimiter

    return RedisRateLimiter.from_url(
        settings.redis_url.get_secret_value(),
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
        namespace=f"{settings.redis_namespace}:ratelimit",
    )


_LIMITERS: dict[str, Callable[[Settings], RateLimiter]] = {
    NullRateLimiter.name: lambda _settings: NullRateLimiter(),
    InMemoryRateLimiter.name: _build_memory,
    "redis": _build_redis,
}

LIMITER_NAMES: tuple[str, ...] = tuple(sorted(_LIMITERS))


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """Construct the limiter named by settings."""
    return _LIMITERS[settings.rate_limit_provider](settings)


@lru_cache(maxsize=1)
def _default_limiter() -> RateLimiter:
    return build_rate_limiter(get_settings())


def get_rate_limiter() -> RateLimiter:
    """The process-wide limiter.

    Cached because the in-memory limiter *is* its own counter: a fresh one per
    request would count every request as the first.
    """
    return _default_limiter()
