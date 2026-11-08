"""Window primitives and the read-only structural view metrics computes over.

Pure domain: no I/O, no wall clock, no infrastructure imports. The metrics
context declares the observation shape it needs as `Protocol`s — any object
with those attributes (e.g. `ingestion.models.NormalizedObservation`) is
accepted without importing the ingestion context.

Containment semantics (docs/05): an observation contributes to an evaluation
window iff its own [window_start, window_end) interval is fully contained in
it. Partial coverage is never silently prorated — it surfaces through the
aggregate's coverage/limitation fields.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from pydantic import model_validator

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import MeasurementKind, QualityState


class BucketLike(Protocol):
    """One histogram bucket read-only view (`le` bound + cumulative count)."""

    @property
    def le(self) -> float | str: ...
    @property
    def count(self) -> int: ...


class MeasurementLike(Protocol):
    """Read-only view of a normalized measurement."""

    @property
    def kind(self) -> MeasurementKind: ...
    @property
    def unit(self) -> str | None: ...
    @property
    def value(self) -> float | None: ...
    @property
    def buckets(self) -> Sequence[BucketLike]: ...
    @property
    def sum(self) -> float | None: ...
    @property
    def count(self) -> int | None: ...
    @property
    def temporality(self) -> str | None: ...


class ObservationLike(Protocol):
    """Read-only view of one runtime observation the metrics context needs."""

    @property
    def service_id(self) -> str: ...
    @property
    def operation_id(self) -> str | None: ...
    @property
    def source_id(self) -> str: ...
    @property
    def window_start(self) -> datetime: ...
    @property
    def window_end(self) -> datetime: ...
    @property
    def quality(self) -> QualityState: ...
    @property
    def measurement(self) -> MeasurementLike: ...
    @property
    def attributes(self) -> Mapping[str, Any]: ...


class TimeWindow(FrozenModel):
    """A half-open [start, end) evaluation window."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if not self.end > self.start:
            raise ValueError("window end must be after window start")
        return self

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    def contains(self, obs: ObservationLike) -> bool:
        """True iff the observation's own interval is fully contained."""
        return obs.window_start >= self.start and obs.window_end <= self.end

    def overlaps(self, obs: ObservationLike) -> bool:
        return obs.window_start < self.end and obs.window_end > self.start


_DURATION_RE = re.compile(r"^\s*(\d+)\s*(s|sec|m|min|h|d)\s*$", re.IGNORECASE)
_DURATION_FACTOR = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "d": 86400}


def parse_duration_seconds(text: str) -> int:
    """Parse '60s'/'15m'/'1h'/'24h'/'7d' into seconds. Raises ValueError."""
    match = _DURATION_RE.match(text)
    if match is None:
        raise ValueError(f"invalid duration: {text!r} (expected e.g. '60s', '15m', '1h', '7d')")
    return int(match.group(1)) * _DURATION_FACTOR[match.group(2).lower()]
