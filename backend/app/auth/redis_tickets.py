"""Redis-backed realtime tickets.

The third module permitted to import the SDK, and for the same reason as the
other two: a ticket issued by one worker must be redeemable by another, which
is the one thing the in-memory store cannot do.

Redemption is a single ``GETDEL``. That matters more than it looks: reading and
then deleting would leave a window in which two sockets could both redeem one
ticket, and single use is half of what makes a ticket in a URL acceptable.
Expiry is Redis's own TTL rather than a timestamp this code checks, so a ticket
cannot outlive its window even if nothing ever asks for it again.
"""

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.auth.tickets import DEFAULT_TTL_SECONDS, new_ticket
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_NAMESPACE = "nexaassist:tickets"


class RedisTicketStore:
    """Tickets shared by every worker."""

    name = "redis"

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        self._redis = client
        self._ttl = ttl_seconds
        self._ns = namespace

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> "RedisTicketStore":
        return cls(
            Redis.from_url(url, decode_responses=True),
            ttl_seconds=ttl_seconds,
            namespace=namespace,
        )

    def _key(self, ticket: str) -> str:
        return f"{self._ns}:{ticket}"

    async def issue(self, subject: str) -> str:
        ticket = new_ticket()
        try:
            await self._redis.set(self._key(ticket), subject, ex=self._ttl)
        except (RedisError, OSError) as exc:
            # Type only: the client's message can carry the connection string.
            # Raising is right here -- unlike rate limiting, a ticket that was
            # never stored cannot be redeemed, so pretending to issue one would
            # hand the client something that is guaranteed to fail.
            logger.warning("realtime ticket issue failed error=%s", type(exc).__name__)
            raise
        logger.info("realtime ticket issued subject=%s", subject)
        return ticket

    async def redeem(self, ticket: str) -> str | None:
        try:
            # One round trip, atomic: read-then-delete would let two sockets
            # redeem the same ticket in the gap.
            subject = await self._redis.getdel(self._key(ticket))
        except (RedisError, OSError) as exc:
            logger.warning("realtime ticket redeem failed error=%s", type(exc).__name__)
            # Fail closed. A ticket that cannot be verified is not a ticket,
            # and this guards a connection rather than protecting capacity.
            return None
        return subject
