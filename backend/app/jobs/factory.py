"""Job queue registry and dependency.

Adding a backend means one entry here and one option on
``Settings.job_queue``; a test asserts the two stay in step, the same way the
embedding provider registry is kept honest.
"""

from collections.abc import Callable
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.jobs.base import JobQueue
from app.jobs.memory import InMemoryJobQueue

_QUEUES: dict[str, Callable[[Settings], JobQueue]] = {
    InMemoryJobQueue.name: lambda _settings: InMemoryJobQueue(),
}

QUEUE_NAMES: tuple[str, ...] = tuple(sorted(_QUEUES))


def build_job_queue(settings: Settings) -> JobQueue:
    """Construct the queue named by settings."""
    return _QUEUES[settings.job_queue](settings)


@lru_cache(maxsize=1)
def _default_queue() -> JobQueue:
    return build_job_queue(get_settings())


def get_job_queue() -> JobQueue:
    """The process-wide job queue.

    Cached because the in-memory backend *is* its own storage: building a fresh
    one per call would hand every caller an empty queue and silently lose work.
    """
    return _default_queue()
