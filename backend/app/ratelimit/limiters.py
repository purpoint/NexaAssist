"""The shipped limiters.

Three, so the protocol is exercised rather than asserted, and so the same
configuration switch covers "off", "one process" and "many processes".
"""

import math
import time
from collections.abc import Callable

from app.core.logging import get_logger
from app.ratelimit.base import RateLimitDecision
from app.ratelimit.errors import RateLimitConfigurationError

logger = get_logger(__name__)


class NullRateLimiter:
    """Allows everything.

    The default, and the reason every endpoint behaves as it did before this
    commit. A real chosen mode, not a stub: a service behind a gateway that
    already rate-limits should not do it twice.
    """

    name = "none"

    @property
    def enforces(self) -> bool:
        return False

    async def check(self, key: str) -> RateLimitDecision:
        return RateLimitDecision(allowed=True)


class InMemoryRateLimiter:
    """A fixed-window counter per key, in this process.

    Deterministic given a clock, which is why the clock is injected -- a test
    that has to sleep to observe a window reset is slow and flaky. Correct for
    a single process and wrong for several, which is exactly what the Redis
    limiter is for.
    """

    name = "memory"

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        _validate(limit, window_seconds)
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._counts: dict[tuple[str, int], int] = {}

    @property
    def enforces(self) -> bool:
        return True

    async def check(self, key: str) -> RateLimitDecision:
        now = self._clock()
        window = int(now // self._window)
        self._prune(window)

        used = self._counts.get((key, window), 0) + 1
        self._counts[(key, window)] = used
        return _decide(used, self._limit, self._window, now)

    def _prune(self, current_window: int) -> None:
        """Drop expired windows.

        Without this the dictionary grows once per key per window forever,
        which is a memory leak wearing a rate limiter's clothes.
        """
        stale = [entry for entry in self._counts if entry[1] < current_window]
        for entry in stale:
            del self._counts[entry]


def _validate(limit: int, window_seconds: int) -> None:
    if limit < 1:
        raise RateLimitConfigurationError(
            "A rate limit must allow at least one request.",
            details={"limit": limit},
        )
    if window_seconds < 1:
        raise RateLimitConfigurationError(
            "A rate limit window must be at least one second.",
            details={"window_seconds": window_seconds},
        )


def _decide(
    used: int, limit: int, window_seconds: int, now: float
) -> RateLimitDecision:
    """Turn a count into a verdict.

    Shared by both counting limiters so "over the limit" means the same thing
    regardless of where the counter lives.
    """
    if used > limit:
        elapsed = now % window_seconds
        return RateLimitDecision(
            allowed=False,
            remaining=0,
            retry_after_seconds=max(1, math.ceil(window_seconds - elapsed)),
        )
    return RateLimitDecision(allowed=True, remaining=limit - used)
