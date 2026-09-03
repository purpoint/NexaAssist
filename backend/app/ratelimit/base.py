"""What a rate limiter is.

Backend-neutral by design: the interface talks about a key, a decision, and a
window, and knows nothing about counters, Redis or memory. That is what lets a
single-process deployment use the in-memory limiter and a multi-process one use
Redis without a single call site changing.

A fixed window rather than a sliding one. A fixed window is one integer per key
per window, which is cheap and, more importantly, means the same key produces
the same answer wherever it is evaluated. Its known weakness is a burst
straddling a boundary allowing up to twice the limit briefly; that is a
documented trade for an implementation whose failure modes are obvious.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class RateLimitDecision(BaseModel):
    """The verdict for one request."""

    model_config = ConfigDict(frozen=True)

    allowed: bool
    remaining: int = Field(
        default=0, ge=0, description="Requests left in the current window."
    )
    retry_after_seconds: int = Field(
        default=0,
        ge=0,
        description="How long until the window resets. Meaningful when refused.",
    )


@runtime_checkable
class RateLimiter(Protocol):
    """Decides whether one more request from a key is allowed."""

    name: str

    @property
    def enforces(self) -> bool:
        """Whether this limiter refuses anything."""
        ...

    async def check(self, key: str) -> RateLimitDecision:
        """Count this request against ``key`` and return the verdict.

        Counts as a side effect: a "check" that did not consume the request
        would let a caller poll the limiter for free. Must not raise for
        ordinary refusal -- a refusal is a decision, and the caller decides
        what HTTP status it becomes.
        """
        ...
