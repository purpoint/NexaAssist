"""The Redis ticket store: shared across workers, and spent exactly once."""

import asyncio

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.auth.redis_tickets import RedisTicketStore

from .conftest import TEST_NAMESPACE

pytestmark = pytest.mark.anyio

TICKET_NS = f"{TEST_NAMESPACE}:tickets"


@pytest.fixture
def store(client: Redis) -> RedisTicketStore:
    return RedisTicketStore(client, ttl_seconds=60, namespace=TICKET_NS)


class BrokenRedis:
    """Every call fails the way an unreachable server fails."""

    async def set(self, *args: object, **kwargs: object) -> None:
        raise RedisConnectionError("Error 61 connecting to redis://user:pw@host:6379")

    async def getdel(self, *args: object) -> str:
        raise RedisConnectionError("Error 61 connecting to redis://user:pw@host:6379")


async def test_a_ticket_redeems_to_its_subject(store: RedisTicketStore) -> None:
    ticket = await store.issue("web-app")
    assert await store.redeem(ticket) == "web-app"


async def test_a_ticket_is_single_use(store: RedisTicketStore) -> None:
    ticket = await store.issue("web-app")
    assert await store.redeem(ticket) == "web-app"
    assert await store.redeem(ticket) is None


async def test_only_one_of_many_racing_redemptions_wins(
    store: RedisTicketStore,
) -> None:
    """The reason redemption is GETDEL and not GET-then-DEL.

    Read-then-delete leaves a window in which two sockets both see the subject
    and both connect, and single use is half of what makes a ticket in a URL
    acceptable.
    """
    ticket = await store.issue("web-app")

    results = await asyncio.gather(*(store.redeem(ticket) for _ in range(10)))

    assert sum(1 for r in results if r == "web-app") == 1
    assert sum(1 for r in results if r is None) == 9


async def test_a_ticket_issued_by_one_store_redeems_in_another(
    client: Redis,
) -> None:
    """The whole point of this backend: workers must agree."""
    issuer = RedisTicketStore(client, ttl_seconds=60, namespace=TICKET_NS)
    redeemer = RedisTicketStore(client, ttl_seconds=60, namespace=TICKET_NS)

    ticket = await issuer.issue("web-app")
    assert await redeemer.redeem(ticket) == "web-app"


async def test_an_unknown_ticket_is_refused(store: RedisTicketStore) -> None:
    assert await store.redeem("never-issued") is None


async def test_a_ticket_carries_a_ttl(store: RedisTicketStore, client: Redis) -> None:
    """Expiry is Redis's own, so a ticket cannot outlive its window."""
    ticket = await store.issue("web-app")
    ttl = await client.ttl(f"{TICKET_NS}:{ticket}")
    assert 0 < ttl <= 60


async def test_keys_stay_inside_the_namespace(
    store: RedisTicketStore, client: Redis
) -> None:
    await store.issue("web-app")
    keys = [key async for key in client.scan_iter(match="*")]
    assert keys and all(key.startswith(TEST_NAMESPACE) for key in keys)


async def test_an_unreachable_redis_refuses_the_ticket() -> None:
    """Fail closed: this guards a connection, it does not protect capacity."""
    store = RedisTicketStore(BrokenRedis(), ttl_seconds=60, namespace=TICKET_NS)
    assert await store.redeem("anything") is None


async def test_issuing_against_an_unreachable_redis_raises() -> None:
    """A ticket that was never stored could never be redeemed.

    Handing one back would give the client something guaranteed to fail.
    """
    store = RedisTicketStore(BrokenRedis(), ttl_seconds=60, namespace=TICKET_NS)
    with pytest.raises(RedisConnectionError):
        await store.issue("web-app")


async def test_an_outage_never_leaks_the_connection_string(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    store = RedisTicketStore(BrokenRedis(), ttl_seconds=60, namespace=TICKET_NS)
    with caplog.at_level(logging.WARNING, logger="app.auth.redis_tickets"):
        await store.redeem("anything")

    assert "user:pw" not in caplog.text
    assert "ConnectionError" in caplog.text
