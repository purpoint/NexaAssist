"""The Redis backend's behaviour that does not need a server.

Key layout, failure translation, and the import boundary are all checkable
offline; that everything actually round-trips through Redis is proven against
a live server in ``tests/backend/redis``.
"""

import ast
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import Settings
from app.core.logging import SecretRedactingFilter
from app.jobs.base import Job, JobStatus
from app.jobs.errors import JobNotFoundError, JobQueueUnavailableError
from app.jobs.factory import QUEUE_NAMES, build_job_queue
from app.jobs.redis_queue import DEFAULT_NAMESPACE, RedisJobQueue

BACKEND_APP = Path(__file__).resolve().parents[2] / "backend" / "app"


class FakePipeline:
    """Records the commands staged against it."""

    def __init__(self, sink: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._sink = sink

    def set(self, *args: Any) -> None:
        self._sink.append(("set", args))

    def rpush(self, *args: Any) -> None:
        self._sink.append(("rpush", args))

    def lrem(self, *args: Any) -> None:
        self._sink.append(("lrem", args))

    async def execute(self) -> None:
        return None


class FakeRedis:
    """Enough of the client surface for the queue, with no server behind it."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}
        self.commands: list[tuple[str, tuple[Any, ...]]] = []

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self.commands)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def llen(self, key: str) -> int:
        return 0

    async def lrange(self, key: str, start: int, stop: int) -> Sequence[str]:
        return []

    async def ping(self) -> bool:
        return True


class BrokenRedis:
    """Every call fails the way an unreachable server fails."""

    def pipeline(self) -> Any:
        raise RedisConnectionError("Error 61 connecting to redis://user:pw@host:6379")

    async def get(self, key: str) -> str:
        raise RedisConnectionError("Error 61 connecting to redis://user:pw@host:6379")

    async def llen(self, key: str) -> int:
        raise RedisConnectionError("nope")

    async def lmove(self, *args: Any) -> str:
        raise RedisConnectionError("nope")

    async def lrange(self, *args: Any) -> Sequence[str]:
        raise RedisConnectionError("nope")

    async def ping(self) -> bool:
        raise RedisConnectionError("nope")


# --------------------------------------------------------------------------
# Keys


def test_keys_all_sit_under_the_namespace() -> None:
    queue = RedisJobQueue(FakeRedis(), namespace="proj:jobs")
    assert queue._pending_key == "proj:jobs:pending"
    assert queue._processing_key == "proj:jobs:processing"
    assert queue._dead_key == "proj:jobs:dead"
    assert queue._job_key("abc") == "proj:jobs:job:abc"


def test_the_default_namespace_is_application_scoped() -> None:
    """A bare key on a shared server is how two projects corrupt each other."""
    assert DEFAULT_NAMESPACE == "nexaassist:jobs"
    assert ":" in DEFAULT_NAMESPACE


@pytest.mark.anyio
async def test_enqueue_writes_the_job_then_queues_the_id() -> None:
    client = FakeRedis()
    queue = RedisJobQueue(client, namespace="ns")
    job = await queue.enqueue("work", {"k": "v"})

    assert [name for name, _ in client.commands] == ["set", "rpush"]
    set_args, rpush_args = client.commands[0][1], client.commands[1][1]
    assert set_args[0] == f"ns:job:{job.id}"
    assert json.loads(set_args[1])["payload"] == {"k": "v"}
    assert rpush_args == ("ns:pending", job.id)


# --------------------------------------------------------------------------
# Failure translation


@pytest.mark.anyio
async def test_a_client_failure_becomes_a_queue_unavailable_error() -> None:
    queue = RedisJobQueue(BrokenRedis(), namespace="ns")
    with pytest.raises(JobQueueUnavailableError):
        await queue.enqueue("work")
    with pytest.raises(JobQueueUnavailableError):
        await queue.get("abc")
    with pytest.raises(JobQueueUnavailableError):
        await queue.depth()
    with pytest.raises(JobQueueUnavailableError):
        await queue.dequeue()
    with pytest.raises(JobQueueUnavailableError):
        await queue.dead_lettered()


@pytest.mark.anyio
async def test_the_unavailable_error_carries_no_connection_string() -> None:
    """The client's own message quotes the URL, password included."""
    queue = RedisJobQueue(BrokenRedis(), namespace="ns")
    with pytest.raises(JobQueueUnavailableError) as caught:
        await queue.get("abc")
    rendered = caught.value.to_response().model_dump_json()
    assert "user:pw" not in rendered
    assert "redis://" not in rendered
    assert caught.value.status_code == 503


@pytest.mark.anyio
async def test_ping_reports_false_rather_than_raising() -> None:
    assert await RedisJobQueue(BrokenRedis()).ping() is False
    assert await RedisJobQueue(FakeRedis()).ping() is True


@pytest.mark.anyio
async def test_a_missing_job_is_not_found_not_unavailable() -> None:
    queue = RedisJobQueue(FakeRedis(), namespace="ns")
    with pytest.raises(JobNotFoundError):
        await queue.get("absent")


@pytest.mark.anyio
async def test_unreadable_stored_bytes_do_not_crash_the_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeRedis({"ns:job:abc": "{not json"})
    queue = RedisJobQueue(client, namespace="ns")
    with caplog.at_level(logging.WARNING, logger="app.jobs.redis_queue"):
        with pytest.raises(JobNotFoundError):
            await queue.get("abc")
    assert "id=abc" in caplog.text
    assert "not json" not in caplog.text


@pytest.mark.anyio
async def test_a_stored_job_round_trips_through_json() -> None:
    job = Job(id="abc", name="work", payload={"k": "v"}, attempts=2)
    client = FakeRedis({"ns:job:abc": job.model_dump_json()})
    queue = RedisJobQueue(client, namespace="ns")
    restored = await queue.get("abc")
    assert restored == job
    assert restored.status is JobStatus.PENDING


@pytest.mark.anyio
async def test_dequeue_on_an_empty_queue_returns_none() -> None:
    class Empty(FakeRedis):
        async def lmove(self, *args: Any) -> None:
            return None

    assert await RedisJobQueue(Empty(), namespace="ns").dequeue() is None


# --------------------------------------------------------------------------
# Configuration and boundaries


def test_the_registry_matches_the_setting() -> None:
    allowed = Settings.model_fields["job_queue"].annotation
    assert set(QUEUE_NAMES) == set(allowed.__args__)
    assert "redis" in QUEUE_NAMES


def test_selecting_redis_without_a_url_is_rejected_at_startup() -> None:
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(job_queue="redis")


def test_the_redis_backend_is_built_from_settings() -> None:
    built = build_job_queue(
        Settings(
            job_queue="redis",
            redis_url="redis://localhost:6379/0",
            redis_namespace="custom:ns",
        )
    )
    assert isinstance(built, RedisJobQueue)
    assert built._pending_key == "custom:ns:pending"


def test_a_blank_redis_url_reads_as_unset() -> None:
    """``.env.example`` ships ``REDIS_URL=`` and must still load."""
    assert Settings(redis_url="").redis_url is None


def test_a_redis_url_with_a_password_is_redacted_from_logs() -> None:
    """No new redaction rule needed: the URL pattern already covers it.

    Worth pinning rather than assuming -- it is the reason the connection
    string does not have to be registered as a literal secret anywhere.
    """
    redacted = SecretRedactingFilter().redact(
        "connecting to redis://default:hunter2@cache.internal:6379/0"
    )
    assert "hunter2" not in redacted
    assert "***REDACTED***" in redacted


def test_only_the_redis_backend_imports_redis() -> None:
    """The same boundary the Groq SDK has: one module, named in requirements."""
    offenders = set()
    for path in BACKEND_APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n == "redis" or n.startswith("redis.") for n in names):
                offenders.add(path.name)
    assert sorted(offenders) == ["redis_queue.py"]
