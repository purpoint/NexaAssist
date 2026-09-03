"""Operational metrics.

Counts and durations, deliberately separate from M16's traces. A trace answers
"what happened in this request"; a metric answers "how often, and how slow,
across all of them". Keeping them apart means neither has to carry the other's
shape.

Vendor-neutral: the recording surface is two methods, so exporting to
Prometheus, StatsD or anything else later is an adapter rather than a rewrite.
No client library is pulled in for a feature that is two dictionaries.

**Label cardinality is the failure mode this module exists to prevent.** A
metrics system dies from unbounded labels long before it dies from volume: one
conversation id in a label and every request creates a new series. So label
values must look like identifiers -- the same no-whitespace rule M16 applies to
span attributes, which excludes prose -- and each metric caps how many distinct
label combinations it will track. Past the cap the combination is dropped and
logged once, which keeps the process alive and tells an operator why a number
stopped moving.
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from app.core.logging import get_logger
from app.observability.spans import sanitise_attributes

logger = get_logger(__name__)

MAX_SERIES_PER_METRIC = 200
"""How many distinct label combinations one metric will track.

Generous for the bounded dimensions this system actually uses -- intent,
handler, outcome, tool name -- and low enough that a mistake is caught while it
is still a bug rather than an outage.
"""

Labels = Mapping[str, str | int | float | bool | None]


@runtime_checkable
class Metrics(Protocol):
    """Where operational numbers go."""

    name: str

    def increment(self, metric: str, labels: Labels | None = None, by: int = 1) -> None:
        """Add to a counter. Must not raise."""
        ...

    def observe(self, metric: str, milliseconds: float, labels: Labels | None = None) -> None:
        """Record a duration. Must not raise."""
        ...


class NullMetrics:
    """Records nothing.

    A real mode, not a stub: a deployment that scrapes nothing should not pay
    to keep dictionaries warm, and expressing that as an implementation means
    no call site needs a branch.
    """

    name = "none"

    def increment(self, metric: str, labels: Labels | None = None, by: int = 1) -> None:
        return None

    def observe(self, metric: str, milliseconds: float, labels: Labels | None = None) -> None:
        return None


class InMemoryMetrics:
    """Keeps counters and duration summaries in this process.

    What the tests assert on, and enough for a single-process deployment to
    expose numbers. Not a time series -- it holds totals, not history.
    """

    name = "memory"

    def __init__(self, *, max_series: int = MAX_SERIES_PER_METRIC) -> None:
        self._max_series = max_series
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._durations: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}
        self._dropped: set[str] = set()

    # -- recording -----------------------------------------------------------

    def increment(self, metric: str, labels: Labels | None = None, by: int = 1) -> None:
        key = self._key(metric, labels, self._counters)
        if key is None:
            return
        self._counters[key] = self._counters.get(key, 0) + by

    def observe(
        self, metric: str, milliseconds: float, labels: Labels | None = None
    ) -> None:
        key = self._key(metric, labels, self._durations)
        if key is None:
            return
        self._durations.setdefault(key, []).append(milliseconds)

    # -- reading -------------------------------------------------------------

    def counter(self, metric: str, labels: Labels | None = None) -> int:
        return self._counters.get((metric, _labels(labels)), 0)

    def total(self, metric: str) -> int:
        """Every series for a counter, summed."""
        return sum(v for (name, _), v in self._counters.items() if name == metric)

    def observations(self, metric: str, labels: Labels | None = None) -> list[float]:
        return list(self._durations.get((metric, _labels(labels)), []))

    def count(self, metric: str, labels: Labels | None = None) -> int:
        return len(self._durations.get((metric, _labels(labels)), []))

    def series(self) -> int:
        return len(self._counters) + len(self._durations)

    def snapshot(self) -> dict[str, object]:
        """A readable summary. Counts and durations only, never a label's origin."""
        return {
            "counters": {
                _render(name, labels): value
                for (name, labels), value in sorted(
                    self._counters.items(), key=lambda item: _render(*item[0])
                )
            },
            "durations": {
                _render(name, labels): {
                    "count": len(values),
                    "total_ms": round(sum(values), 3),
                }
                for (name, labels), values in sorted(
                    self._durations.items(), key=lambda item: _render(*item[0])
                )
            },
        }

    def clear(self) -> None:
        self._counters.clear()
        self._durations.clear()
        self._dropped.clear()

    # -- internals -----------------------------------------------------------

    def _key(
        self, metric: str, labels: Labels | None, store: dict
    ) -> tuple[str, tuple[tuple[str, str], ...]] | None:
        key = (metric, _labels(labels))
        if key in store:
            return key
        if sum(1 for existing in store if existing[0] == metric) >= self._max_series:
            if metric not in self._dropped:
                # Once per metric: a cardinality explosion must not also be a
                # log explosion.
                self._dropped.add(metric)
                logger.warning(
                    "metric label cardinality capped metric=%s limit=%d",
                    metric,
                    self._max_series,
                )
            return None
        return key


def _labels(labels: Labels | None) -> tuple[tuple[str, str], ...]:
    """Normalise labels into a hashable, sorted, content-free key.

    Values go through the M16 attribute rule, so prose becomes ``<omitted>``
    rather than a new series per customer message. Sorted so the same labels in
    a different order are the same series.
    """
    if not labels:
        return ()
    safe = sanitise_attributes(labels)
    return tuple(sorted((str(k), str(v)) for k, v in safe.items()))


def _render(metric: str, labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return metric
    rendered = ",".join(f"{k}={v}" for k, v in labels)
    return f"{metric}{{{rendered}}}"
