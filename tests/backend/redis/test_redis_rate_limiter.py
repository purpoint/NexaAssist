"""The Redis rate limiter: shared counting, and what it does when Redis is gone."""

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.ratelimit.redis_limiter import RedisRateLimiter

from .conftest import TEST_NAMESPACE

pytestmark = pytest.mark.anyio

LIMIT_NS = f"{TEST_NAMESPACE}:ratelimit"


@pytest.fixture
def limiter(client: Redis) -> RedisRateLimiter:
    return RedisRateLimiter(
        client, limit=2, window_seconds=60, namespace=LIMIT_NS
    )


class BrokenRedis:
    """Every call fails the way an unreachable server fails."""

    def pipeline(self) -> object:
        raise RedisConnectionError("Error 61 connecting to redis://user:pw@host:6379")

    async def time(self) -> tuple[int, int]:
        raise RedisConnectionError("nope")


async def test_requests_under_the_limit_are_allowed(limiter: RedisRateLimiter) -> None:
    assert (await limiter.check("web")).allowed is True
    assert (await limiter.check("web")).allowed is True


async def test_the_request_after_the_limit_is_refused(
    limiter: RedisRateLimiter,
) -> None:
    for _ in range(2):
        await limiter.check("web")
    refused = await limiter.check("web")
    assert refused.allowed is False
    assert refused.retry_after_seconds > 0


async def test_keys_are_counted_separately(limiter: RedisRateLimiter) -> None:
    for _ in range(2):
        await limiter.check("web")
    assert (await limiter.check("worker")).allowed is True


async def test_two_limiters_share_one_count(client: Redis) -> None:
    """The whole reason this backend exists: several processes must agree."""
    first = RedisRateLimiter(client, limit=2, window_seconds=60, namespace=LIMIT_NS)
    second = RedisRateLimiter(client, limit=2, window_seconds=60, namespace=LIMIT_NS)

    assert (await first.check("web")).allowed is True
    assert (await second.check("web")).allowed is True
    assert (await first.check("web")).allowed is False


async def test_the_counter_expires(limiter: RedisRateLimiter, client: Redis) -> None:
    """Without a TTL every key a caller ever used would live forever."""
    await limiter.check("web")
    keys = [key async for key in client.scan_iter(match=f"{LIMIT_NS}:web:*")]
    assert keys
    assert await client.ttl(keys[0]) > 0


async def test_the_ttl_is_not_extended_by_later_requests(
    limiter: RedisRateLimiter, client: Redis
) -> None:
    await limiter.check("web")
    keys = [key async for key in client.scan_iter(match=f"{LIMIT_NS}:web:*")]
    first = await client.ttl(keys[0])
    await limiter.check("web")
    assert await client.ttl(keys[0]) <= first


async def test_an_unreachable_redis_allows_the_request() -> None:
    """Rate limiting protects capacity; it is not an authorization control.

    Failing closed would turn a cache outage into a total outage, which is the
    worse failure.
    """
    limiter = RedisRateLimiter(
        BrokenRedis(), limit=1, window_seconds=60, namespace=LIMIT_NS
    )
    decision = await limiter.check("web")
    assert decision.allowed is True


async def test_an_outage_never_leaks_the_connection_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    limiter = RedisRateLimiter(
        BrokenRedis(), limit=1, window_seconds=60, namespace=LIMIT_NS
    )
    with caplog.at_level(logging.WARNING, logger="app.ratelimit.redis_limiter"):
        await limiter.check("web")
    assert "user:pw" not in caplog.text
    assert "ConnectionError" in caplog.text


async def test_keys_stay_inside_the_namespace(
    limiter: RedisRateLimiter, client: Redis
) -> None:
    await limiter.check("web")
    keys = [key async for key in client.scan_iter(match="*")]
    assert keys and all(key.startswith(TEST_NAMESPACE) for key in keys)
