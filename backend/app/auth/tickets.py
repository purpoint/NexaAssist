"""Short-lived tickets for authenticating a WebSocket.

A browser cannot set a header on a WebSocket handshake. The alternatives are a
cookie, a subprotocol, or a query parameter, and none of them is a good place
for a long-lived key: a query string reaches proxy logs, browser history and
referrers, and a cookie brings CSRF along with it.

So the key never touches the socket. A client presents it over authenticated
HTTP, receives a ticket, and spends the ticket on the handshake. What ends up
in a URL is therefore something that is:

* **short-lived** — a minute by default, so a leaked log line is stale almost
  immediately;
* **single-use** — redeeming destroys it, so a replayed URL buys nothing;
* **opaque and random** — not derived from the key, so recovering the key from
  a ticket is not a computation anyone can do;
* **bound to a subject** — it authenticates exactly the principal that asked
  for it, and nothing else.

Redemption is a lookup by key, never a comparison, so there is no string to
compare in variable time.

Two implementations, because a ticket issued by one worker must be redeemable
by another: in-memory is correct for a single process, Redis for several. The
protocol is what the socket depends on.
"""

import secrets
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from app.core.logging import get_logger

logger = get_logger(__name__)

TICKET_BYTES = 32
"""256 bits of entropy. A guessable ticket is a bypass, not an inconvenience."""

DEFAULT_TTL_SECONDS = 60
"""Long enough to open a socket, short enough that a leaked one is useless."""


def new_ticket() -> str:
    """An opaque, URL-safe token with no relationship to any credential."""
    return secrets.token_urlsafe(TICKET_BYTES)


@runtime_checkable
class TicketStore(Protocol):
    """Issues tickets and redeems them exactly once."""

    name: str

    async def issue(self, subject: str) -> str:
        """Mint a ticket for ``subject`` and return it."""
        ...

    async def redeem(self, ticket: str) -> str | None:
        """Return the subject and destroy the ticket, or ``None``.

        ``None`` covers every failure the caller must treat identically:
        unknown, expired, and already spent. Distinguishing them would tell an
        attacker which guesses were once real.
        """
        ...


class InMemoryTicketStore:
    """Tickets held in this process.

    Correct for a single worker and wrong for several -- a ticket issued by one
    process is unknown to the next, which is exactly what the Redis store is
    for. The clock is injected so expiry can be tested without sleeping.
    """

    name = "memory"

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._tickets: dict[str, tuple[str, float]] = {}

    async def issue(self, subject: str) -> str:
        self._prune()
        ticket = new_ticket()
        self._tickets[ticket] = (subject, self._clock() + self._ttl)
        # Subject only. The ticket is a credential for as long as it lives.
        logger.info("realtime ticket issued subject=%s", subject)
        return ticket

    async def redeem(self, ticket: str) -> str | None:
        # pop, not get: redeeming is what spends it, and a store that returned
        # the subject before deleting would let two sockets share one ticket.
        entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        subject, expires_at = entry
        if self._clock() >= expires_at:
            return None
        return subject

    def _prune(self) -> None:
        """Drop expired tickets.

        Without this an unredeemed ticket lives until the process restarts,
        which is a slow memory leak wearing a credential's clothes.
        """
        now = self._clock()
        for ticket in [t for t, (_, exp) in self._tickets.items() if now >= exp]:
            del self._tickets[ticket]

    def __len__(self) -> int:
        return len(self._tickets)
