"""The queue contract, proven against a real Redis server.

Deliberately the same assertions as the in-memory backend in
``tests/backend/test_job_queue.py``. Two implementations of one protocol are
only interchangeable if the same statements hold for both, and the way to know
that is to write them twice against the same contract.
"""

import pytest
from redis.asyncio import Redis

from app.jobs.base import JobStatus
from app.jobs.errors import JobNotFoundError
from app.jobs.redis_queue import RedisJobQueue

from .conftest import TEST_NAMESPACE

pytestmark = pytest.mark.anyio


@pytest.fixture
def queue(client: Redis) -> RedisJobQueue:
    return RedisJobQueue(client, namespace=TEST_NAMESPACE)


async def test_the_server_answers(queue: RedisJobQueue) -> None:
    assert await queue.ping() is True


async def test_enqueue_then_read_back(queue: RedisJobQueue) -> None:
    job = await queue.enqueue("send_email", {"to": "someone"})
    stored = await queue.get(job.id)
    assert stored == job
    assert stored.status is JobStatus.PENDING
    assert await queue.depth() == 1


async def test_dequeue_is_first_in_first_out(queue: RedisJobQueue) -> None:
    first = await queue.enqueue("first")
    second = await queue.enqueue("second")
    assert (await queue.dequeue()).id == first.id
    assert (await queue.dequeue()).id == second.id
    assert await queue.dequeue() is None


async def test_dequeue_marks_running_and_counts_the_attempt(
    queue: RedisJobQueue,
) -> None:
    await queue.enqueue("work")
    taken = await queue.dequeue()
    assert taken.status is JobStatus.RUNNING
    assert taken.attempts == 1
    assert (await queue.get(taken.id)).attempts == 1
    assert await queue.depth() == 0


async def test_an_in_flight_job_is_held_not_dropped(
    queue: RedisJobQueue, client: Redis
) -> None:
    """A worker that dies must leave the id somewhere it can still be seen."""
    job = await queue.enqueue("work")
    await queue.dequeue()
    assert await client.lrange(f"{TEST_NAMESPACE}:processing", 0, -1) == [job.id]


async def test_completion_clears_the_in_flight_list(
    queue: RedisJobQueue, client: Redis
) -> None:
    await queue.enqueue("work")
    done = await queue.complete(await queue.dequeue())
    assert done.status is JobStatus.SUCCEEDED
    assert (await queue.get(done.id)).status is JobStatus.SUCCEEDED
    assert await client.lrange(f"{TEST_NAMESPACE}:processing", 0, -1) == []


async def test_failure_requeues_while_attempts_remain(
    queue: RedisJobQueue, client: Redis
) -> None:
    await queue.enqueue("work", max_attempts=2)
    failed = await queue.fail(await queue.dequeue(), "transient")
    assert failed.status is JobStatus.PENDING
    assert failed.error == "transient"
    assert await queue.depth() == 1
    assert await client.lrange(f"{TEST_NAMESPACE}:processing", 0, -1) == []
    assert await queue.dead_lettered() == []


async def test_a_requeued_job_goes_to_the_back(queue: RedisJobQueue) -> None:
    first = await queue.enqueue("first", max_attempts=3)
    second = await queue.enqueue("second", max_attempts=3)
    await queue.fail(await queue.dequeue(), "transient")
    assert (await queue.dequeue()).id == second.id
    assert (await queue.dequeue()).id == first.id


async def test_failure_dead_letters_once_attempts_are_spent(
    queue: RedisJobQueue,
) -> None:
    await queue.enqueue("work", max_attempts=1)
    dead = await queue.fail(await queue.dequeue(), "permanent")
    assert dead.status is JobStatus.FAILED
    assert await queue.depth() == 0
    assert [job.id for job in await queue.dead_lettered()] == [dead.id]


async def test_a_job_is_retried_exactly_max_attempts_times(
    queue: RedisJobQueue,
) -> None:
    await queue.enqueue("work", max_attempts=3)
    seen = 0
    while (taken := await queue.dequeue()) is not None:
        seen += 1
        await queue.fail(taken, "still broken")
    assert seen == 3
    assert len(await queue.dead_lettered()) == 1


async def test_an_unknown_job_id_is_not_found(queue: RedisJobQueue) -> None:
    with pytest.raises(JobNotFoundError):
        await queue.get("nope")


async def test_payloads_survive_the_round_trip(queue: RedisJobQueue) -> None:
    payload = {"a": [1, 2, {"b": None}], "c": True, "d": 1.5, "e": "text"}
    job = await queue.enqueue("work", payload)
    assert (await queue.get(job.id)).payload == payload


async def test_two_queues_on_one_server_do_not_see_each_other(
    client: Redis,
) -> None:
    """The namespace is what makes a shared server safe."""
    mine = RedisJobQueue(client, namespace=f"{TEST_NAMESPACE}:a")
    theirs = RedisJobQueue(client, namespace=f"{TEST_NAMESPACE}:b")
    await mine.enqueue("work")
    assert await mine.depth() == 1
    assert await theirs.depth() == 0
    assert await theirs.dequeue() is None
