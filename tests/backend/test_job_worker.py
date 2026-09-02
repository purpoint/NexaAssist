"""Worker dispatch, failure classification, and the handler registry.

Runs against the in-memory queue with stub handlers: what is under test is the
worker's decision-making, not any particular piece of domain work.
"""

import asyncio
import logging

import pytest
from pydantic import BaseModel

from app.jobs.base import JobStatus
from app.jobs.errors import JobDefinitionError, JobNotFoundError
from app.jobs.handlers import JobError, JobHandler, JobHandlerRegistry
from app.jobs.memory import InMemoryJobQueue
from app.jobs.worker import JobOutcome, JobWorker

pytestmark = pytest.mark.anyio


class Payload(BaseModel):
    value: int = 0


class Recorder:
    """Succeeds, remembering what it was handed."""

    name = "recorder"
    payload = Payload

    def __init__(self) -> None:
        self.seen: list[int] = []

    async def run(self, params: BaseModel) -> None:
        self.seen.append(params.value)


class Exploding:
    """Fails with an unexpected exception, the way a bug does."""

    name = "exploding"
    payload = Payload

    async def run(self, params: BaseModel) -> None:
        raise RuntimeError("connection to postgresql://user:pw@host lost")


class Refusing:
    """Fails deliberately, and says a retry cannot help."""

    name = "refusing"
    payload = Payload

    async def run(self, params: BaseModel) -> None:
        raise JobError("This will never work.", retryable=False)


class Transient:
    """Fails deliberately, and says a retry might help."""

    name = "transient"
    payload = Payload

    async def run(self, params: BaseModel) -> None:
        raise JobError("Try again shortly.", retryable=True)


class Hanging:
    name = "hanging"
    payload = Payload

    async def run(self, params: BaseModel) -> None:
        await asyncio.sleep(10)


def registry_with(*handlers: JobHandler) -> JobHandlerRegistry:
    registry = JobHandlerRegistry()
    for handler in handlers:
        registry.register(handler)
    return registry


# --------------------------------------------------------------------------
# The registry


def test_a_handler_must_declare_a_pydantic_payload() -> None:
    class NoModel:
        name = "bad"
        payload = dict

    with pytest.raises(JobDefinitionError):
        JobHandlerRegistry().register(NoModel())


def test_a_handler_name_is_validated() -> None:
    class BadName:
        name = "Not Valid"
        payload = Payload

    with pytest.raises(JobDefinitionError):
        JobHandlerRegistry().register(BadName())


def test_duplicate_registration_is_refused() -> None:
    """Silently replacing would make behaviour depend on import order."""
    registry = registry_with(Recorder())
    with pytest.raises(JobDefinitionError):
        registry.register(Recorder())


def test_an_unregistered_name_is_not_found() -> None:
    with pytest.raises(JobNotFoundError):
        JobHandlerRegistry().get("absent")


def test_names_are_sorted_and_counted() -> None:
    registry = registry_with(Recorder(), Transient())
    assert registry.names() == ["recorder", "transient"]
    assert len(registry) == 2
    assert registry.has("recorder") and not registry.has("nope")


def test_the_stubs_satisfy_the_protocol() -> None:
    assert isinstance(Recorder(), JobHandler)


# --------------------------------------------------------------------------
# Dispatch


async def test_an_empty_queue_yields_nothing() -> None:
    worker = JobWorker(InMemoryJobQueue(), registry_with(Recorder()))
    assert await worker.run_once() is None


async def test_a_successful_job_is_completed() -> None:
    queue, handler = InMemoryJobQueue(), Recorder()
    worker = JobWorker(queue, registry_with(handler))
    job = await queue.enqueue("recorder", {"value": 7})

    run = await worker.run_once()
    assert run.outcome is JobOutcome.SUCCEEDED
    assert run.ok and run.attempts == 1 and run.error is None
    assert handler.seen == [7]
    assert (await queue.get(job.id)).status is JobStatus.SUCCEEDED


async def test_an_unregistered_handler_dead_letters_without_retrying() -> None:
    """Registering a handler is a deploy, not a retry."""
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, JobHandlerRegistry())
    job = await queue.enqueue("nobody_handles_this", max_attempts=5)

    run = await worker.run_once()
    assert run.outcome is JobOutcome.DEAD_LETTERED
    assert run.attempts == 1
    assert "nobody_handles_this" in run.error
    assert (await queue.get(job.id)).status is JobStatus.FAILED
    assert await queue.depth() == 0


async def test_a_payload_that_does_not_match_dead_letters_immediately() -> None:
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Recorder()))
    await queue.enqueue("recorder", {"value": "not an integer"}, max_attempts=5)

    run = await worker.run_once()
    assert run.outcome is JobOutcome.DEAD_LETTERED
    assert "value" in run.error
    assert await queue.depth() == 0


async def test_the_validation_message_omits_the_offending_value() -> None:
    """Pydantic renders the input alongside the error; that input is user data."""
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Recorder()))
    await queue.enqueue("recorder", {"value": "secret-account-number"})

    run = await worker.run_once()
    assert "secret-account-number" not in run.error


async def test_a_non_retryable_job_error_dead_letters_immediately() -> None:
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Refusing()))
    await queue.enqueue("refusing", max_attempts=5)

    run = await worker.run_once()
    assert run.outcome is JobOutcome.DEAD_LETTERED
    assert run.error == "This will never work."
    assert len(await queue.dead_lettered()) == 1


async def test_a_retryable_job_error_is_requeued() -> None:
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Transient()))
    await queue.enqueue("transient", max_attempts=2)

    run = await worker.run_once()
    assert run.outcome is JobOutcome.RETRYING
    assert await queue.depth() == 1


async def test_an_unexpected_exception_is_retried_then_dead_lettered() -> None:
    """Unlike a tool call: the usual cause is a dependency that was briefly down."""
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Exploding()))
    await queue.enqueue("exploding", max_attempts=2)

    first = await worker.run_once()
    assert first.outcome is JobOutcome.RETRYING
    second = await worker.run_once()
    assert second.outcome is JobOutcome.DEAD_LETTERED
    assert second.attempts == 2


async def test_an_unexpected_exception_never_reaches_the_caller() -> None:
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Exploding()))
    await queue.enqueue("exploding")
    run = await worker.run_once()
    assert run.error == "The job failed unexpectedly."


async def test_an_unexpected_exception_message_is_never_recorded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its text here carries a connection string, password included."""
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Exploding()))
    await queue.enqueue("exploding")

    with caplog.at_level(logging.WARNING, logger="app.jobs.worker"):
        run = await worker.run_once()
    assert "user:pw" not in run.error
    assert "user:pw" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_a_job_that_hangs_is_timed_out_and_retried() -> None:
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Hanging()), timeout_seconds=0.01)
    await queue.enqueue("hanging", max_attempts=2)

    run = await worker.run_once()
    assert run.outcome is JobOutcome.RETRYING
    assert "0.01s" in run.error


# --------------------------------------------------------------------------
# Draining


async def test_drain_runs_everything_then_stops() -> None:
    queue, handler = InMemoryJobQueue(), Recorder()
    worker = JobWorker(queue, registry_with(handler))
    for value in range(3):
        await queue.enqueue("recorder", {"value": value})

    runs = await worker.drain()
    assert [run.outcome for run in runs] == [JobOutcome.SUCCEEDED] * 3
    assert handler.seen == [0, 1, 2]
    assert await queue.depth() == 0


async def test_drain_respects_its_budget() -> None:
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Recorder()))
    for value in range(5):
        await queue.enqueue("recorder", {"value": value})

    assert len(await worker.drain(max_jobs=2)) == 2
    assert await queue.depth() == 3


async def test_drain_cannot_spin_forever_on_a_failing_job() -> None:
    """A requeued job lands back in the same queue this loop is reading."""
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Transient()))
    await queue.enqueue("transient", max_attempts=1000)

    runs = await worker.drain(max_jobs=4)
    assert len(runs) == 4
    assert all(run.outcome is JobOutcome.RETRYING for run in runs)


async def test_drain_on_an_empty_queue_returns_nothing() -> None:
    worker = JobWorker(InMemoryJobQueue(), registry_with(Recorder()))
    assert await worker.drain() == []


async def test_logs_carry_identifiers_not_payloads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    queue = InMemoryJobQueue()
    worker = JobWorker(queue, registry_with(Recorder()))
    job = await queue.enqueue("recorder", {"value": 4242})

    with caplog.at_level(logging.INFO, logger="app.jobs.worker"):
        await worker.run_once()
    assert f"id={job.id}" in caplog.text
    assert "outcome=succeeded" in caplog.text
    assert "4242" not in caplog.text
