"""Redis-backed job queue.

The durable, cross-process counterpart to the in-memory backend. Same
protocol, same validation, same attempt accounting -- what changes is only
where the state lives, which is the point of having the protocol at all.

The only module permitted to import ``redis``. Everything else depends on
:class:`~app.jobs.base.JobQueue`.

Key layout, all under one configurable namespace so a shared server can host
more than one thing safely::

    {ns}:pending      list of job ids waiting to be taken
    {ns}:processing   list of job ids currently held by a worker
    {ns}:dead         list of job ids that exhausted their attempts
    {ns}:job:{id}     the job itself, as JSON

**Handoff is at-least-once.** ``dequeue`` uses ``LMOVE`` to shift the id from
``pending`` to ``processing`` in a single server-side operation, so a worker
that dies mid-job leaves the id visible in ``processing`` rather than dropping
it on the floor. Reclaiming those stranded ids is deliberately not implemented
here -- doing it correctly needs a lease clock and an owner, which is a larger
design than this milestone calls for. What matters is that the id is still
there to reclaim.

Every Redis failure becomes :class:`JobQueueUnavailableError`. Client errors
are never re-raised as themselves: a ``redis`` exception can carry the
connection string, and that string can carry a password.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.jobs.base import (
    DEFAULT_MAX_ATTEMPTS,
    Job,
    JobStatus,
    build_job,
)
from app.jobs.errors import JobNotFoundError, JobQueueUnavailableError

logger = get_logger(__name__)

DEFAULT_NAMESPACE = "nexaassist:jobs"


class RedisJobQueue:
    """A job queue whose state lives in Redis."""

    name = "redis"

    def __init__(
        self,
        client: Redis,
        *,
        namespace: str = DEFAULT_NAMESPACE,
    ) -> None:
        self._redis = client
        self._ns = namespace

    @classmethod
    def from_url(cls, url: str, *, namespace: str = DEFAULT_NAMESPACE) -> "RedisJobQueue":
        """Build a queue from a connection URL.

        ``decode_responses`` is on so values come back as ``str``: the payload
        is JSON, and decoding it in one place beats scattering ``.decode()``
        through every read.
        """
        return cls(Redis.from_url(url, decode_responses=True), namespace=namespace)

    # -- keys ---------------------------------------------------------------

    @property
    def _pending_key(self) -> str:
        return f"{self._ns}:pending"

    @property
    def _processing_key(self) -> str:
        return f"{self._ns}:processing"

    @property
    def _dead_key(self) -> str:
        return f"{self._ns}:dead"

    def _job_key(self, job_id: str) -> str:
        return f"{self._ns}:job:{job_id}"

    # -- queue protocol -----------------------------------------------------

    async def enqueue(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> Job:
        job = build_job(name, payload, max_attempts=max_attempts)
        async with self._guard():
            pipe = self._redis.pipeline()
            pipe.set(self._job_key(job.id), job.model_dump_json())
            pipe.rpush(self._pending_key, job.id)
            await pipe.execute()
        logger.info("job enqueued id=%s name=%s", job.id, job.name)
        return job

    async def dequeue(self) -> Job | None:
        async with self._guard():
            job_id = await self._redis.lmove(
                self._pending_key, self._processing_key, "LEFT", "RIGHT"
            )
        if job_id is None:
            return None

        stored = await self.get(job_id)
        running = stored.model_copy(
            update={"status": JobStatus.RUNNING, "attempts": stored.attempts + 1}
        )
        await self._write(running)
        logger.info(
            "job dequeued id=%s name=%s attempt=%d",
            running.id,
            running.name,
            running.attempts,
        )
        return running

    async def complete(self, job: Job) -> Job:
        stored = await self.get(job.id)
        done = stored.model_copy(update={"status": JobStatus.SUCCEEDED, "error": None})
        async with self._guard():
            pipe = self._redis.pipeline()
            pipe.set(self._job_key(done.id), done.model_dump_json())
            pipe.lrem(self._processing_key, 1, done.id)
            await pipe.execute()
        logger.info("job succeeded id=%s name=%s", done.id, done.name)
        return done

    async def fail(self, job: Job, error: str) -> Job:
        stored = await self.get(job.id)

        if stored.exhausted:
            dead = stored.model_copy(update={"status": JobStatus.FAILED, "error": error})
            async with self._guard():
                pipe = self._redis.pipeline()
                pipe.set(self._job_key(dead.id), dead.model_dump_json())
                pipe.lrem(self._processing_key, 1, dead.id)
                pipe.rpush(self._dead_key, dead.id)
                await pipe.execute()
            logger.warning(
                "job dead-lettered id=%s name=%s attempts=%d",
                dead.id,
                dead.name,
                dead.attempts,
            )
            return dead

        retry = stored.model_copy(update={"status": JobStatus.PENDING, "error": error})
        async with self._guard():
            pipe = self._redis.pipeline()
            pipe.set(self._job_key(retry.id), retry.model_dump_json())
            pipe.lrem(self._processing_key, 1, retry.id)
            # Back of the queue: a job that just failed must not starve the
            # work already waiting behind it.
            pipe.rpush(self._pending_key, retry.id)
            await pipe.execute()
        logger.info(
            "job requeued id=%s name=%s attempts=%d", retry.id, retry.name, retry.attempts
        )
        return retry

    async def get(self, job_id: str) -> Job:
        async with self._guard():
            raw = await self._redis.get(self._job_key(job_id))
        if raw is None:
            raise JobNotFoundError(details={"job": job_id})
        return self._parse(raw, job_id)

    async def depth(self) -> int:
        async with self._guard():
            return int(await self._redis.llen(self._pending_key))

    async def dead_lettered(self) -> Sequence[Job]:
        async with self._guard():
            ids = await self._redis.lrange(self._dead_key, 0, -1)
            if not ids:
                return []
            raws = await self._redis.mget([self._job_key(i) for i in ids])
        return [
            self._parse(raw, job_id)
            for job_id, raw in zip(ids, raws, strict=True)
            if raw is not None
        ]

    async def ping(self) -> bool:
        """Never raises: a probe that throws is useless to a readiness check."""
        try:
            return bool(await self._redis.ping())
        except (RedisError, OSError) as exc:
            logger.warning("redis probe failed error=%s", type(exc).__name__)
            return False

    async def aclose(self) -> None:
        """Release the connection pool."""
        await self._redis.aclose()

    # -- internals ----------------------------------------------------------

    async def _write(self, job: Job) -> None:
        async with self._guard():
            await self._redis.set(self._job_key(job.id), job.model_dump_json())

    def _parse(self, raw: str, job_id: str) -> Job:
        try:
            return Job.model_validate(json.loads(raw))
        except (ValueError, TypeError) as exc:
            # Something else wrote to our key, or a job outlived a schema
            # change. Neither is recoverable here, and the stored bytes are not
            # ours to quote back.
            logger.warning(
                "stored job could not be read id=%s error=%s", job_id, type(exc).__name__
            )
            raise JobNotFoundError(details={"job": job_id}) from None

    def _guard(self) -> "_RedisGuard":
        return _RedisGuard()


class _RedisGuard:
    """Turns any client failure into the application's own error type.

    A context manager rather than a decorator so it can wrap the exact
    statements that talk to the server, leaving pure computation outside it.
    """

    async def __aenter__(self) -> "_RedisGuard":
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object) -> bool:
        if exc_type is None:
            return False
        if issubclass(exc_type, (RedisError, OSError)):
            logger.warning("redis operation failed error=%s", exc_type.__name__)
            raise JobQueueUnavailableError(details={"backend": "redis"}) from None
        return False
