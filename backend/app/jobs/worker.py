"""Running queued work.

The worker is the background counterpart to
:class:`~app.tools.execution.ToolExecutor`, and it exists to enforce the same
rule: nothing a handler does may escape as an exception. There is no request
to fail and no user watching, so an escaping error would end the worker and
quietly stop every job behind it.

Where it deliberately differs from the tool executor is what it does with an
*unexpected* exception. A tool call reports it and moves on; a job retries it,
because the most common cause of an unexpected failure in background work is a
dependency that was briefly unavailable, and the attempt budget already bounds
how long that can go on. Failures that could never succeed on a second attempt
-- an unregistered handler, a payload that does not match its schema -- are
marked non-retryable and dead-lettered on the spot.

One thing is allowed to escape: a queue that is itself unreachable. The worker
cannot record an outcome without the queue, and swallowing that would spin.
"""

import asyncio
import time
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.logging import get_logger
from app.jobs.base import Job, JobQueue, JobStatus
from app.jobs.errors import JobNotFoundError
from app.jobs.handlers import JobError, JobHandler, JobHandlerRegistry
from app.observability.metrics import Metrics, NullMetrics

logger = get_logger(__name__)

DEFAULT_JOB_TIMEOUT_SECONDS = 30.0
DEFAULT_DRAIN_LIMIT = 100

UNEXPECTED_ERROR = "The job failed unexpectedly."


class JobOutcome(StrEnum):
    """How one attempt ended, from the worker's point of view."""

    SUCCEEDED = "succeeded"
    RETRYING = "retrying"
    DEAD_LETTERED = "dead_lettered"


class JobRun(BaseModel):
    """The record of one attempt."""

    model_config = ConfigDict(frozen=True)

    job_id: str
    name: str
    outcome: JobOutcome
    attempts: int
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome is JobOutcome.SUCCEEDED


class JobWorker:
    """Takes jobs off a queue and runs the handler each one names."""

    def __init__(
        self,
        queue: JobQueue,
        registry: JobHandlerRegistry,
        *,
        timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
        metrics: Metrics | None = None,
    ) -> None:
        self._queue = queue
        self._registry = registry
        self._timeout = timeout_seconds
        # Optional and defaulted, so every existing caller is unchanged.
        self._metrics = metrics if metrics is not None else NullMetrics()

    async def run_once(self) -> JobRun | None:
        """Run the next job, or return ``None`` when there is nothing to do.

        An empty queue is not an error and does not deserve an exception: a
        caller polling one should be able to write a plain loop.
        """
        job = await self._queue.dequeue()
        if job is None:
            return None
        return await self._dispatch(job)

    async def drain(self, *, max_jobs: int = DEFAULT_DRAIN_LIMIT) -> list[JobRun]:
        """Run jobs until the queue is empty or the budget is spent.

        Bounded on purpose. A job that fails and is re-queued lands back in
        this same queue, so an unbounded drain against a permanently failing
        job would spin until its attempts ran out -- with no way for a caller
        to regain control in between.
        """
        runs: list[JobRun] = []
        for _ in range(max_jobs):
            run = await self.run_once()
            if run is None:
                break
            runs.append(run)
        return runs

    # -- internals ----------------------------------------------------------

    async def _dispatch(self, job: Job) -> JobRun:
        started = time.perf_counter()

        try:
            handler = self._registry.get(job.name)
        except JobNotFoundError:
            # Registering the handler is a deploy, not a retry.
            return await self._fail(
                job,
                f"No handler is registered for {job.name!r}.",
                started,
                retryable=False,
            )

        try:
            params = handler.payload.model_validate(job.payload)
        except ValidationError as exc:
            # Field paths and messages only. Pydantic renders the offending
            # input alongside them, and that input is caller data.
            return await self._fail(
                job, _describe_validation(exc), started, retryable=False
            )

        return await self._run(job, handler, params, started)

    async def _run(
        self, job: Job, handler: JobHandler, params: BaseModel, started: float
    ) -> JobRun:
        try:
            async with asyncio.timeout(self._timeout):
                await handler.run(params)
        except TimeoutError:
            return await self._fail(
                job,
                f"The job did not finish within {self._timeout:g}s.",
                started,
                retryable=True,
            )
        except JobError as exc:
            # Deliberate and expected: the message was written to be read.
            return await self._fail(
                job, exc.message, started, retryable=exc.retryable
            )
        except Exception as exc:
            # A bug, or a dependency that is briefly down. The type is logged;
            # the recorded error says nothing that could carry internals into
            # an operator's console or a future prompt.
            logger.warning(
                "job raised an unexpected error id=%s name=%s error=%s",
                job.id,
                job.name,
                type(exc).__name__,
            )
            return await self._fail(job, UNEXPECTED_ERROR, started, retryable=True)

        completed = await self._queue.complete(job)
        duration = _elapsed_ms(started)
        logger.info(
            "job run id=%s name=%s outcome=%s duration_ms=%.1f",
            job.id,
            job.name,
            JobOutcome.SUCCEEDED.value,
            duration,
        )
        self._record(job.name, JobOutcome.SUCCEEDED, duration)
        return JobRun(
            job_id=job.id,
            name=job.name,
            outcome=JobOutcome.SUCCEEDED,
            attempts=completed.attempts,
            duration_ms=duration,
        )

    async def _fail(
        self, job: Job, error: str, started: float, *, retryable: bool
    ) -> JobRun:
        recorded = await self._queue.fail(job, error, retryable=retryable)
        outcome = (
            JobOutcome.DEAD_LETTERED
            if recorded.status is JobStatus.FAILED
            else JobOutcome.RETRYING
        )
        duration = _elapsed_ms(started)
        logger.info(
            "job run id=%s name=%s outcome=%s attempts=%d duration_ms=%.1f",
            job.id,
            job.name,
            outcome.value,
            recorded.attempts,
            duration,
        )
        self._record(job.name, outcome, duration)
        return JobRun(
            job_id=job.id,
            name=job.name,
            outcome=outcome,
            attempts=recorded.attempts,
            error=error,
            duration_ms=duration,
        )


    def _record(self, name: str, outcome: JobOutcome, duration_ms: float) -> None:
        """Job name and outcome are bounded; the payload is never a label."""
        labels = {"job": name, "outcome": outcome.value}
        self._metrics.increment("job_runs_total", labels)
        self._metrics.observe("job_duration_ms", duration_ms, {"job": name})


def _describe_validation(exc: ValidationError) -> str:
    parts = [
        f"{'.'.join(str(p) for p in err['loc']) or 'payload'}: {err['msg']}"
        for err in exc.errors()
    ]
    return "Invalid payload — " + "; ".join(parts)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0