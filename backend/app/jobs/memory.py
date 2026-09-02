"""An in-process job queue.

The deterministic offline counterpart to the Redis backend, and the default:
the suite must never need a server running, and the service must start without
one. Not a toy -- it enforces exactly the same validation, attempt accounting,
and dead-lettering as the real backend, which is what makes a test written
against it mean something.

What it is not is durable or shared. Everything lives in one process's memory,
so a restart loses the queue and two workers in two processes see two different
queues. That is the whole reason the Redis backend exists.
"""

from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.logging import get_logger
from app.jobs.base import (
    DEFAULT_MAX_ATTEMPTS,
    Job,
    JobStatus,
    build_job,
)
from app.jobs.errors import JobNotFoundError

logger = get_logger(__name__)


class InMemoryJobQueue:
    """FIFO queue backed by a deque, with every job retained by id."""

    name = "memory"

    def __init__(self) -> None:
        self._pending: deque[str] = deque()
        self._jobs: dict[str, Job] = {}
        self._dead: list[str] = []

    async def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        job = build_job(name, payload, max_attempts=max_attempts)
        self._jobs[job.id] = job
        self._pending.append(job.id)
        # Identifier and name only: a payload is caller data.
        logger.info("job enqueued id=%s name=%s", job.id, job.name)
        return job

    async def dequeue(self) -> Job | None:
        while self._pending:
            job_id = self._pending.popleft()
            stored = self._jobs.get(job_id)
            if stored is None:  # pragma: no cover - defensive
                continue
            running = stored.model_copy(
                update={"status": JobStatus.RUNNING, "attempts": stored.attempts + 1}
            )
            self._jobs[job_id] = running
            logger.info(
                "job dequeued id=%s name=%s attempt=%d",
                running.id,
                running.name,
                running.attempts,
            )
            return running
        return None

    async def complete(self, job: Job) -> Job:
        done = self._stored(job.id).model_copy(
            update={"status": JobStatus.SUCCEEDED, "error": None}
        )
        self._jobs[job.id] = done
        logger.info("job succeeded id=%s name=%s", done.id, done.name)
        return done

    async def fail(self, job: Job, error: str) -> Job:
        stored = self._stored(job.id)
        if stored.exhausted:
            dead = stored.model_copy(update={"status": JobStatus.FAILED, "error": error})
            self._jobs[job.id] = dead
            self._dead.append(job.id)
            logger.warning(
                "job dead-lettered id=%s name=%s attempts=%d",
                dead.id,
                dead.name,
                dead.attempts,
            )
            return dead

        retry = stored.model_copy(update={"status": JobStatus.PENDING, "error": error})
        self._jobs[job.id] = retry
        # Back of the queue, not the front: a job that just failed should not
        # starve everything queued behind it by retrying immediately.
        self._pending.append(job.id)
        logger.info(
            "job requeued id=%s name=%s attempts=%d", retry.id, retry.name, retry.attempts
        )
        return retry

    async def get(self, job_id: str) -> Job:
        return self._stored(job_id)

    async def depth(self) -> int:
        return len(self._pending)

    async def dead_lettered(self) -> Sequence[Job]:
        return [self._jobs[job_id] for job_id in self._dead]

    async def ping(self) -> bool:
        """Always reachable: it is this process."""
        return True

    def _stored(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFoundError(details={"job": job_id}) from None
