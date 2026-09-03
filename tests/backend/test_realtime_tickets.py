"""Realtime tickets: single use, short life, and nothing derived from the key."""

import logging

import pytest

from app.auth.factory import TICKET_STORE_NAMES, build_ticket_store
from app.auth.tickets import (
    DEFAULT_TTL_SECONDS,
    TICKET_BYTES,
    InMemoryTicketStore,
    TicketStore,
    new_ticket,
)
from app.core.config import Settings

pytestmark = pytest.mark.anyio

KEY = "web-app-key-0123456789abcdef"
KEYS = f"web-app:{KEY}"


class Clock:
    """A clock a test moves by hand, so expiry needs no sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def store(ttl: int = 60) -> tuple[InMemoryTicketStore, Clock]:
    clock = Clock()
    return InMemoryTicketStore(ttl_seconds=ttl, clock=clock), clock


# --------------------------------------------------------------------------
# The token itself


def test_a_ticket_is_long_and_unguessable() -> None:
    """A guessable ticket is a bypass, not an inconvenience."""
    first, second = new_ticket(), new_ticket()
    assert first != second
    # token_urlsafe expands, so the string is longer than the byte count.
    assert len(first) >= TICKET_BYTES


def test_a_ticket_is_url_safe() -> None:
    """It is spent in a query string; padding or slashes would need escaping."""
    allowed = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    )
    assert set(new_ticket()) <= allowed


async def test_a_ticket_is_not_derived_from_the_key() -> None:
    """Recovering the key from a ticket must not be a computation."""
    made, _ = store()
    ticket = await made.issue("web-app")
    assert KEY not in ticket
    assert "web-app" not in ticket


# --------------------------------------------------------------------------
# Issue and redeem


async def test_a_ticket_redeems_to_its_subject() -> None:
    made, _ = store()
    ticket = await made.issue("web-app")
    assert await made.redeem(ticket) == "web-app"


async def test_a_ticket_is_single_use() -> None:
    made, _ = store()
    ticket = await made.issue("web-app")

    assert await made.redeem(ticket) == "web-app"
    assert await made.redeem(ticket) is None


async def test_an_unknown_ticket_is_refused() -> None:
    made, _ = store()
    assert await made.redeem("never-issued") is None


async def test_an_empty_ticket_is_refused() -> None:
    made, _ = store()
    assert await made.redeem("") is None


async def test_a_ticket_expires() -> None:
    made, clock = store(ttl=60)
    ticket = await made.issue("web-app")

    clock.now = 60.0
    assert await made.redeem(ticket) is None


async def test_a_ticket_survives_until_it_expires() -> None:
    made, clock = store(ttl=60)
    ticket = await made.issue("web-app")

    clock.now = 59.9
    assert await made.redeem(ticket) == "web-app"


async def test_every_failure_looks_the_same() -> None:
    """Unknown, expired and spent are indistinguishable to the caller.

    Telling them apart would say which guesses were once real tickets.
    """
    made, clock = store(ttl=10)
    spent = await made.issue("web-app")
    await made.redeem(spent)
    expired = await made.issue("web-app")
    clock.now = 10.0

    assert await made.redeem(spent) is None
    assert await made.redeem(expired) is None
    assert await made.redeem("never-issued") is None


async def test_two_subjects_get_distinct_tickets() -> None:
    made, _ = store()
    web = await made.issue("web-app")
    worker = await made.issue("worker")

    assert web != worker
    assert await made.redeem(web) == "web-app"
    assert await made.redeem(worker) == "worker"


async def test_expired_tickets_are_pruned() -> None:
    """Otherwise an unredeemed ticket lives until the process restarts."""
    made, clock = store(ttl=10)
    for _ in range(5):
        await made.issue("web-app")
    assert len(made) == 5

    clock.now = 20.0
    await made.issue("web-app")
    assert len(made) == 1


# --------------------------------------------------------------------------
# Nothing leaks


async def test_issuing_logs_the_subject_not_the_ticket(
    caplog: pytest.LogCaptureFixture,
) -> None:
    made, _ = store()
    with caplog.at_level(logging.INFO, logger="app.auth.tickets"):
        ticket = await made.issue("web-app")

    assert "subject=web-app" in caplog.text
    assert ticket not in caplog.text


# --------------------------------------------------------------------------
# Wiring


def test_both_stores_satisfy_the_protocol() -> None:
    from app.auth.redis_tickets import RedisTicketStore

    assert isinstance(InMemoryTicketStore(), TicketStore)
    assert isinstance(RedisTicketStore(object()), TicketStore)


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["realtime_ticket_store"].annotation
    assert set(TICKET_STORE_NAMES) == set(allowed.__args__)


def test_the_default_store_is_in_memory() -> None:
    assert isinstance(build_ticket_store(Settings()), InMemoryTicketStore)


def test_the_redis_store_needs_a_url() -> None:
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(realtime_ticket_store="redis")


def test_the_ttl_is_short_by_default() -> None:
    """Long enough to open a socket; short enough that a leak goes stale."""
    assert DEFAULT_TTL_SECONDS <= 120
    assert Settings().realtime_ticket_ttl_seconds == DEFAULT_TTL_SECONDS


def test_the_ttl_cannot_be_set_absurdly_long() -> None:
    with pytest.raises(ValueError):
        Settings(realtime_ticket_ttl_seconds=86_400)
