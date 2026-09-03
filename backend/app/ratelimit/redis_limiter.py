"""Redis-backed rate limiting.

Reuses the Redis this project already configures -- the same ``REDIS_URL`` and
the same namespace convention as the M13 job queue -- rather than introducing a
second notion of "our Redis". What it does not reuse is ``RedisJobQueue``: a
queue and a counter share a server, not a purpose.

The counter is the standard ``INCR`` plus ``EXPIRE`` on first use, issued as
one pipeline so a crash between them cannot leave a key that never expires.
Counting server-side is the entire point: several application processes must
agree, and that is the one thing the in-memory limiter cannot do.

**A limiter that cannot reach Redis allows the request.** Rate limiting protects
capacity; it is not an authorization control. Failing closed would turn a cache
outage into a total outage, which is a worse failure than briefly serving more
traffic than intended. The outage is logged by type -- never with the URL,
which carries the host and may carry a password.
"""

import math
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.ratelimit.base import RateLimitDecision
from app.ratelimit.limiters import _validate

logger = get_logger(__name__)

DEFAULT_NAMESPACE = "nexaassist:ratelimit"


class RedisRateLimiter:
    """A fixed-window counter shared by every process."""

    name = "redis"

    def __init__(
        self,
        client: Redis,
        *,
        limit: int,
        window_seconds: int,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        _validate(limit, window_seconds)
        self._redis = client
        self._limit = limit
        self._window = window_seconds
        self._ns = namespace

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        limit: int,
        window_seconds: int,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> "RedisRateLimiter":
        return cls(
            Redis.from_url(url, decode_responses=True),
            limit=limit,
            window_seconds=window_seconds,
            namespace=namespace,
        )

    @property
    def enforces(self) -> bool:
        return True

    def _key(self, key: str, window: int) -> str:
        return f"{self._ns}:{key}:{window}"

    async def check(self, key: str) -> RateLimitDecision:
        window, elapsed = await self._window_now()
        counter = self._key(key, window)

        try:
            pipe = self._redis.pipeline()
            pipe.incr(counter)
            # Set only on creation, so a long-running window is not extended
            # by every request inside it.
            pipe.expire(counter, self._window, nx=True)
            used = int((await pipe.execute())[0])
        except (RedisError, OSError) as exc:
            # Type only: the client's message can carry the connection string.
            logger.warning(
                "rate limiter unavailable error=%s outcome=allowed",
                type(exc).__name__,
            )
            return RateLimitDecision(allowed=True)

        if used > self._limit:
            return RateLimitDecision(
                allowed=False,
                remaining=0,
                retry_after_seconds=max(1, math.ceil(self._window - elapsed)),
            )
        return RateLimitDecision(allowed=True, remaining=self._limit - used)

    async def _window_now(self) -> tuple[int, float]:
        """Derive the window from the server's clock where possible.

        Several application processes must agree on which window they are in,
        and their own clocks may not. A server that cannot be asked falls back
        to local time rather than failing -- being briefly in the wrong window
        is better than refusing traffic.
        """
        try:
            seconds, microseconds = await self._redis.time()
            now = float(seconds) + float(microseconds) / 1_000_000
        except (RedisError, OSError, TypeError, ValueError):
            import time

            now = time.time()
        return int(now // self._window), now % self._window

    async def aclose(self) -> None:
        await self._redis.aclose()
