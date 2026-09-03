"""Enforcing the rate limit on a request.

The HTTP half, kept out of ``app.ratelimit`` so that package stays free of
FastAPI -- the same separation authentication uses.

Keyed by the authenticated subject rather than by client address. A subject is
what the deployment actually issued and what it can revoke; a source address
is shared by everyone behind one NAT and trivially changed by anyone who
minds. Where authentication is off every request shares the anonymous bucket,
which is honest: without an identity there is nothing finer to limit by.
"""

from typing import Annotated

from fastapi import Depends

from app.api.v1.identity import require_identity
from app.auth.identity import RequestIdentity
from app.core.logging import get_logger
from app.ratelimit.base import RateLimiter
from app.ratelimit.errors import RateLimitExceededError
from app.ratelimit.factory import get_rate_limiter

logger = get_logger(__name__)


async def enforce_rate_limit(
    identity: Annotated[RequestIdentity, Depends(require_identity)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> RequestIdentity:
    """Count this request and refuse it if the caller is over the limit.

    Returns the identity so a route can depend on this instead of
    ``require_identity`` and get both, rather than resolving the identity
    twice.
    """
    decision = await limiter.check(identity.subject)
    if not decision.allowed:
        # Subject and outcome only. No counts from other callers, no window
        # arithmetic, and nothing about the backend.
        logger.warning("rate limit exceeded subject=%s", identity.subject)
        raise RateLimitExceededError(decision.retry_after_seconds)
    return identity
