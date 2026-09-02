"""The job queue contract, exercised against the in-memory backend.

Written against the protocol rather than the implementation wherever it can
be: the Redis backend arriving next has to satisfy exactly these assertions,
and a test that reaches for a deque would not carry over.
"""

import logging

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.jobs.base import (
    DEFAULT_MAX_ATTEMPTS,
    MAX_NAME_LENGTH,
    JobQueue,
    JobStatus,
    build_job,
    validate_job_name,
    validate_payload,
)
from app.jobs.errors import JobDefinitionError, JobNotFoundError, JobPayloadError
from app.jobs.factory import QUEUE_NAMES, build_job_queue, get_job_queue
from app.jobs.memory import InMemoryJobQueue


@pytest.fixture
def queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


# --------------------------------------------------------------------------
# The value type


def test_a_job_is_frozen() -> None:
    job = build_job("send_email")
    with pytest.raises(ValidationError):
        job.id = "reassigned"  # type: ignore[misc]


def test_a_new_job_starts_pending_with_no_attempts() -> None:
    job = build_job("send_email", {"to": "someone"})
    assert job.status is JobStatus.PENDING
    assert job.attempts == 0
    assert job.max_attempts == DEFAULT_MAX_ATTEMPTS
    assert job.error is None
    assert job.payload == {"to": "someone"}


def test_ids_are_unique() -> None:
    assert build_job("work").id != build_job("work").id


def test_exhausted_reflects_the_attempt_budget() -> None:
    job = build_job("work", max_attempts=2)
    assert not job.exhausted
    assert not job.model_copy(update={"attempts": 1}).exhausted
    assert job.model_copy(update={"attempts": 2}).exhausted


def test_terminal_covers_both_end_states() -> None:
    job = build_job("work")
    assert not job.terminal
    assert job.model_copy(update={"status": JobStatus.RUNNING}).terminal is False
    assert job.model_copy(update={"status": JobStatus.SUCCEEDED}).terminal
    assert job.model_copy(update={"status": JobStatus.FAILED}).terminal


# --------------------------------------------------------------------------
# Validation, shared by every backend


@pytest.mark.parametrize("name", ["", "Upper", "with-dash", "with space", "sym!"])
def test_malformed_names_are_rejected(name: str) -> None:
    with pytest.raises(JobDefinitionError):
        validate_job_name(name)


def test_an_overlong_name_is_rejected() -> None:
    validate_job_name("a" * MAX_NAME_LENGTH)
    with pytest.raises(JobDefinitionError):
        validate_job_name("a" * (MAX_NAME_LENGTH + 1))


def test_a_missing_payload_becomes_an_empty_dict() -> None:
    assert validate_payload(None) == {}


def test_a_non_mapping_payload_is_rejected() -> None:
    with pytest.raises(JobPayloadError):
        validate_payload(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_an_unserialisable_payload_is_rejected() -> None:
    with pytest.raises(JobPayloadError):
        validate_payload({"when": object()})


def test_a_payload_that_changes_shape_in_json_is_rejected() -> None:
    """A tuple survives ``dumps`` and comes back a list.

    Accepting it would mean the in-memory backend held a tuple while Redis
    returned a list for the very same job -- the exact divergence this check
    exists to prevent.
    """
    with pytest.raises(JobPayloadError):
        validate_payload({"ids": (1, 2)})


def test_a_payload_with_non_string_keys_is_rejected() -> None:
    """``json.dumps`` coerces an int key to a string rather than failing."""
    with pytest.raises(JobPayloadError):
        validate_payload({1: "one"})


def test_nested_json_safe_payloads_are_accepted() -> None:
    payload = {"a": [1, 2, {"b": None}], "c": True, "d": 1.5, "e": "text"}
    assert validate_payload(payload) == payload


def test_an_attempt_budget_below_one_is_rejected() -> None:
    with pytest.raises(JobDefinitionError):
        build_job("work", max_attempts=0)


def test_the_payload_is_copied_not_aliased() -> None:
    supplied = {"k": "v"}
    job = build_job("work", supplied)
    supplied["k"] = "mutated"
    assert job.payload == {"k": "v"}


# --------------------------------------------------------------------------
# Queue behaviour


def test_the_memory_queue_satisfies_the_protocol() -> None:
    assert isinstance(InMemoryJobQueue(), JobQueue)


@pytest.mark.anyio
async def test_enqueue_stores_a_pending_job(queue: InMemoryJobQueue) -> None:
    job = await queue.enqueue("send_email", {"to": "someone"})
    assert job.status is JobStatus.PENDING
    assert await queue.depth() == 1
    assert (await queue.get(job.id)) == job


@pytest.mark.anyio
async def test_enqueue_validates_through_the_queue(queue: InMemoryJobQueue) -> None:
    with pytest.raises(JobDefinitionError):
        await queue.enqueue("Not Valid")
    with pytest.raises(JobPayloadError):
        await queue.enqueue("work", {"bad": object()})
    assert await queue.depth() == 0


@pytest.mark.anyio
async def test_dequeue_is_first_in_first_out(queue: InMemoryJobQueue) -> None:
    first = await queue.enqueue("first")
    second = await queue.enqueue("second")
    assert (await queue.dequeue()).id == first.id
    assert (await queue.dequeue()).id == second.id


@pytest.mark.anyio
async def test_dequeue_marks_running_and_counts_the_attempt(
    queue: InMemoryJobQueue,
) -> None:
    await queue.enqueue("work")
    taken = await queue.dequeue()
    assert taken is not None
    assert taken.status is JobStatus.RUNNING
    assert taken.attempts == 1
    assert await queue.depth() == 0


@pytest.mark.anyio
async def test_dequeue_on_an_empty_queue_returns_none(queue: InMemoryJobQueue) -> None:
    assert await queue.dequeue() is None


@pytest.mark.anyio
async def test_complete_marks_the_job_succeeded(queue: InMemoryJobQueue) -> None:
    await queue.enqueue("work")
    taken = await queue.dequeue()
    done = await queue.complete(taken)
    assert done.status is JobStatus.SUCCEEDED
    assert done.error is None
    assert (await queue.get(done.id)).status is JobStatus.SUCCEEDED


@pytest.mark.anyio
async def test_complete_clears_an_earlier_failure_message(
    queue: InMemoryJobQueue,
) -> None:
    await queue.enqueue("work", max_attempts=2)
    await queue.fail(await queue.dequeue(), "first attempt broke")
    done = await queue.complete(await queue.dequeue())
    assert done.status is JobStatus.SUCCEEDED
    assert done.error is None


@pytest.mark.anyio
async def test_failure_requeues_while_attempts_remain(queue: InMemoryJobQueue) -> None:
    await queue.enqueue("work", max_attempts=2)
    failed = await queue.fail(await queue.dequeue(), "transient")
    assert failed.status is JobStatus.PENDING
    assert failed.error == "transient"
    assert await queue.depth() == 1
    assert await queue.dead_lettered() == []


@pytest.mark.anyio
async def test_a_requeued_job_goes_to_the_back(queue: InMemoryJobQueue) -> None:
    """A failing job must not starve the work queued behind it."""
    first = await queue.enqueue("first", max_attempts=3)
    second = await queue.enqueue("second", max_attempts=3)
    await queue.fail(await queue.dequeue(), "transient")
    assert (await queue.dequeue()).id == second.id
    assert (await queue.dequeue()).id == first.id


@pytest.mark.anyio
async def test_failure_dead_letters_once_attempts_are_spent(
    queue: InMemoryJobQueue,
) -> None:
    await queue.enqueue("work", max_attempts=1)
    dead = await queue.fail(await queue.dequeue(), "permanent")
    assert dead.status is JobStatus.FAILED
    assert dead.attempts == 1
    assert await queue.depth() == 0
    assert [job.id for job in await queue.dead_lettered()] == [dead.id]


@pytest.mark.anyio
async def test_a_job_is_retried_exactly_max_attempts_times(
    queue: InMemoryJobQueue,
) -> None:
    await queue.enqueue("work", max_attempts=3)
    seen = 0
    while (taken := await queue.dequeue()) is not None:
        seen += 1
        await queue.fail(taken, "still broken")
    assert seen == 3
    assert len(await queue.dead_lettered()) == 1


@pytest.mark.anyio
async def test_an_unknown_job_id_is_not_found(queue: InMemoryJobQueue) -> None:
    with pytest.raises(JobNotFoundError):
        await queue.get("nope")


@pytest.mark.anyio
async def test_the_memory_queue_is_always_reachable(queue: InMemoryJobQueue) -> None:
    assert await queue.ping() is True


@pytest.mark.anyio
async def test_logs_record_identifiers_never_payloads(
    queue: InMemoryJobQueue, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.jobs.memory"):
        job = await queue.enqueue("send_email", {"to": "person@example.com"})
        await queue.fail(await queue.dequeue(), "boom")
    assert f"id={job.id}" in caplog.text
    assert "name=send_email" in caplog.text
    assert "person@example.com" not in caplog.text


# --------------------------------------------------------------------------
# Wiring


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["job_queue"].annotation
    assert set(QUEUE_NAMES) == set(allowed.__args__)


def test_the_configured_backend_is_built() -> None:
    built = build_job_queue(Settings(job_queue="memory"))
    assert isinstance(built, InMemoryJobQueue)


def test_an_unknown_backend_is_rejected_by_settings() -> None:
    with pytest.raises(ValidationError):
        Settings(job_queue="rabbitmq")


def test_the_default_queue_is_a_single_shared_instance() -> None:
    """A fresh queue per call would silently drop every job already enqueued."""
    assert get_job_queue() is get_job_queue()
