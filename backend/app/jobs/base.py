"""What a background job is, and what a queue must do with one.

Mirrors the LLM and embedding layers deliberately: a ``Protocol`` with a real
implementation and a deterministic offline one, so the application depends on
the contract rather than on Redis being present.

Two decisions worth stating, because both constrain everything downstream:

* **A job carries no timestamps.** Ordering is the queue's job, and FIFO
  insertion order is all any caller here needs. Putting a clock inside the
  value would make every test that compares jobs depend on when it ran.
* **A payload must be JSON round-trippable, and that is checked at enqueue.**
  The in-memory queue would happily hold any Python object; Redis would not.
  Validating in one shared place is what stops the two backends from diverging
  into "works in tests, fails in production".
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from app.jobs.errors import JobDefinitionError, JobPayloadError

MAX_NAME_LENGTH = 64
DEFAULT_MAX_ATTEMPTS = 3

_ALLOWED_NAME = set("abcdefghijklmnopqrstuvwxyz0123456789_")


class JobStatus(StrEnum):
    """Where a job is in its life cycle."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(BaseModel):
    """One unit of deferred work.

    Frozen: a job is a value describing a state, and transitions produce a new
    value rather than mutating one a caller may still be holding. The same
    discipline as :class:`~app.tools.results.ToolResult`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str = Field(description="Which handler should run this job.")
    payload: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    attempts: int = Field(
        default=0, ge=0, description="How many times this job has been dequeued."
    )
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1)
    error: str | None = Field(
        default=None,
        description=(
            "Why the last attempt failed, phrased for an operator. Never an "
            "exception type, a traceback, or a connection string."
        ),
    )

    @property
    def exhausted(self) -> bool:
        """Whether the attempt budget is spent, so a retry is not owed."""
        return self.attempts >= self.max_attempts

    @property
    def terminal(self) -> bool:
        return self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED)


@runtime_checkable
class JobQueue(Protocol):
    """Somewhere to put work now and take it out later.

    Every method is async even where an implementation needs no await: the
    protocol is what callers are written against, and a backend that talks to a
    server over a socket must be able to satisfy it.
    """

    name: str

    async def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        """Add work to the back of the queue and return the stored job."""
        ...

    async def dequeue(self) -> Job | None:
        """Take the next job, marking it running and counting the attempt.

        Returns ``None`` when there is nothing to do -- an empty queue is not
        an error, and a worker polling one should not have to catch anything.
        """
        ...

    async def complete(self, job: Job) -> Job:
        """Record that a job finished successfully."""
        ...

    async def fail(self, job: Job, error: str) -> Job:
        """Record a failed attempt.

        Re-queues the job when attempts remain, and dead-letters it when they
        do not. Which of the two happened is visible in the returned status, so
        a caller never has to recompute the retry arithmetic itself.
        """
        ...

    async def get(self, job_id: str) -> Job:
        """Return a stored job, raising ``JobNotFoundError`` when absent."""
        ...

    async def depth(self) -> int:
        """How many jobs are waiting to be dequeued."""
        ...

    async def dead_lettered(self) -> Sequence[Job]:
        """Jobs that exhausted their attempts, oldest first."""
        ...

    async def ping(self) -> bool:
        """Whether the backend is reachable right now. Never raises."""
        ...


def validate_job_name(name: str) -> str:
    """Reject a name no backend could safely use as part of a key.

    Same character set as the tool registry: a name travels into Redis keys and
    log lines, and one that needs quoting in either is a name worth refusing.
    """
    if not name or not set(name) <= _ALLOWED_NAME:
        raise JobDefinitionError(
            "Job names must be non-empty and use only lowercase letters, "
            "digits, and underscores.",
            details={"name": str(name)},
        )
    if len(name) > MAX_NAME_LENGTH:
        raise JobDefinitionError(
            f"Job names must be at most {MAX_NAME_LENGTH} characters.",
            details={"name": name},
        )
    return name


def validate_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the payload as a plain dict, proving it survives JSON first.

    The round trip is the point: ``json.dumps`` alone would accept a tuple and
    hand back a list, so a job that looked fine on the way in would come out of
    Redis a different shape. Checking both directions here means the in-memory
    backend rejects exactly what the Redis one would.
    """
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise JobPayloadError(
            "A job payload must be a mapping.",
            details={"type": type(payload).__name__},
        )
    plain = dict(payload)
    try:
        restored = json.loads(json.dumps(plain))
    except (TypeError, ValueError) as exc:
        # Type only. The message would quote the offending value, which may be
        # customer content.
        raise JobPayloadError(details={"reason": type(exc).__name__}) from None
    if restored != plain:
        raise JobPayloadError(
            "A job payload must survive a JSON round trip unchanged.",
            details={"reason": "not_round_trippable"},
        )
    return plain


def new_job_id() -> str:
    """An opaque identifier. Hex rather than a UUID string: it ends up in keys."""
    return uuid.uuid4().hex


def build_job(
    name: str,
    payload: Mapping[str, Any] | None = None,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Job:
    """Validate inputs and construct a pending job.

    Shared by every backend so validation cannot drift between them.
    """
    if max_attempts < 1:
        raise JobDefinitionError(
            "max_attempts must be at least 1.",
            details={"max_attempts": str(max_attempts)},
        )
    return Job(
        id=new_job_id(),
        name=validate_job_name(name),
        payload=validate_payload(payload),
        max_attempts=max_attempts,
    )
