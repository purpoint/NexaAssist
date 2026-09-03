"""Tracer registry and dependency.

Adding a recorder means one entry here and one option on
``Settings.trace_recorder``; a test asserts the two stay in step, as it does
for the embedding providers and the job queues.
"""

from collections.abc import Callable
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.observability.cost import PricingTable
from app.observability.tracer import (
    InMemoryRecorder,
    LoggingRecorder,
    NullRecorder,
    Recorder,
    Tracer,
)

_RECORDERS: dict[str, Callable[[], Recorder]] = {
    LoggingRecorder.name: LoggingRecorder,
    InMemoryRecorder.name: InMemoryRecorder,
    NullRecorder.name: NullRecorder,
}

RECORDER_NAMES: tuple[str, ...] = tuple(sorted(_RECORDERS))


def build_recorder(settings: Settings) -> Recorder:
    """Construct the recorder named by settings."""
    return _RECORDERS[settings.trace_recorder]()


def build_tracer(settings: Settings) -> Tracer:
    return Tracer(build_recorder(settings))


@lru_cache(maxsize=1)
def _default_tracer() -> Tracer:
    return build_tracer(get_settings())


def get_tracer() -> Tracer:
    """The process-wide tracer.

    Cached because the in-memory recorder *is* its own storage: a fresh tracer
    per call would hand every caller an empty one.
    """
    return _default_tracer()


def build_pricing_table(settings: Settings) -> PricingTable:
    """Parse the configured price list.

    Raises at construction rather than at accounting time: a malformed price
    should surface while somebody is looking at the deploy.
    """
    return PricingTable.from_entries(settings.llm_pricing)


@lru_cache(maxsize=1)
def _default_pricing() -> PricingTable:
    return build_pricing_table(get_settings())


def get_pricing_table() -> PricingTable:
    """The process-wide price list."""
    return _default_pricing()
