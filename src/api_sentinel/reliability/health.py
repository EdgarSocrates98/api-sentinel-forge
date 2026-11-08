"""Service health semantics — missing data is never fabricated health (LAB-007).

Three distinct missing-data conditions, observable in API output:

- `no_traffic` — a fresh source reports request measurements; the service was
  observed but served no requests. Idle ≠ down ≠ unmeasurable.
- `source_stale` — sources exist and sent data before, but the newest window
  is older than `stale_after_seconds`. Collection failed; nothing can be said
  about the service right now.
- `failing` — fresh request data shows the error ratio at the unavailability
  threshold. Service unavailability is asserted only on evidence.

`state` uses the ADR-018 vocabulary (AVAILABLE | UNAVAILABLE | PARTIAL |
UNVERIFIED | UNKNOWN); quality stays a separate dimension.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import QualityState
from api_sentinel.metrics.aggregates import (
    ErrorClassification,
    WindowAggregate,
    compute_window_aggregates,
)
from api_sentinel.metrics.windows import ObservationLike, TimeWindow


class HealthState(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    PARTIAL = "PARTIAL"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"


class HealthCondition(StrEnum):
    """Why the state is what it is — the three-way distinction is here."""

    OBSERVED = "observed"  # fresh traffic, no error evidence
    DEGRADED = "degraded"  # fresh traffic, some requests failing
    FAILING = "failing"  # fresh traffic, error ratio at unavailability threshold
    NO_TRAFFIC = "no_traffic"  # fresh request measurements, zero requests
    UNMEASURABLE = "unmeasurable"  # fresh observations, none usable for rates
    SOURCE_STALE = "source_stale"  # collection stopped; last data is old
    NO_OBSERVATIONS = "no_observations"  # nothing ever observed


class HealthPolicy(FrozenModel):
    """Tunables for health evaluation (defaults per docs/05 processing policy)."""

    stale_after_seconds: int = Field(default=300, gt=0)
    unavailable_error_ratio: float = Field(default=1.0, gt=0.0, le=1.0)


class SourceHealth(FrozenModel):
    """Per-source collection status — a silent source is reported, not hidden."""

    source_id: str
    registered: bool
    observations: int
    latest_window_end: datetime | None
    stale: bool  # has data, but the newest window is old


class ServiceHealthReport(FrozenModel):
    """Per-service health surface: state + latest indicators + unknowns."""

    service_id: str
    state: HealthState
    condition: HealthCondition
    evaluated_at: datetime
    latest_window_start: datetime | None
    latest_window_end: datetime | None
    latest_indicators: WindowAggregate | None
    absent_indicators: list[str] = Field(default_factory=list)
    quality: QualityState
    sources: list[SourceHealth] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


_INDICATOR_FIELDS = (
    "request_rate",
    "error_ratio",
    "timeout_ratio",
    "latency_p50_ms",
    "latency_p95_ms",
    "latency_p99_ms",
    "latency_mean_ms",
)


def _absent(aggregate: WindowAggregate | None) -> list[str]:
    """Indicators a source cannot supply are reported absent — never interpolated."""
    if aggregate is None:
        return list(_INDICATOR_FIELDS)
    return [name for name in _INDICATOR_FIELDS if getattr(aggregate, name) is None]


def assess_service_health(
    *,
    service_id: str,
    observations: Iterable[ObservationLike],
    source_ids: Iterable[str],
    now: datetime,
    classification: ErrorClassification | None = None,
    policy: HealthPolicy | None = None,
) -> ServiceHealthReport:
    """Evaluate current health for one service from stored observations.

    Pure function — `now` is the injected clock reading. Only the latest
    complete observation window feeds the indicators.
    """
    policy = policy or HealthPolicy()
    obs = [o for o in observations if o.service_id == service_id]
    registered = set(source_ids)
    unknowns: list[str] = []
    limitations: list[str] = []

    by_source: dict[str, list[ObservationLike]] = {}
    for o in obs:
        by_source.setdefault(o.source_id, []).append(o)
    staleness_horizon = now - timedelta(seconds=policy.stale_after_seconds)
    sources: list[SourceHealth] = []
    for source_id in sorted(registered | set(by_source)):
        source_obs = by_source.get(source_id, [])
        latest_end = max((o.window_end for o in source_obs), default=None)
        stale = latest_end is not None and latest_end < staleness_horizon
        if latest_end is None:
            unknowns.append(f"source_silent:{source_id}")
        elif stale:
            unknowns.append(f"source_stale:{source_id}")
        sources.append(
            SourceHealth(
                source_id=source_id,
                registered=source_id in registered,
                observations=len(source_obs),
                latest_window_end=latest_end,
                stale=stale,
            )
        )

    if not obs:
        if registered:
            unknowns.append("sources_registered_but_never_observed")
        else:
            unknowns.append("no_telemetry_sources")
        return ServiceHealthReport(
            service_id=service_id,
            state=HealthState.UNKNOWN,
            condition=HealthCondition.NO_OBSERVATIONS,
            evaluated_at=now,
            latest_window_start=None,
            latest_window_end=None,
            latest_indicators=None,
            absent_indicators=_absent(None),
            quality=QualityState.UNKNOWN,
            sources=sources,
            unknowns=sorted(unknowns),
            limitations=[],
        )

    latest_end = max(o.window_end for o in obs)
    if latest_end < staleness_horizon:
        # Every datum is old: collection has stopped — UNVERIFIED, never down.
        return ServiceHealthReport(
            service_id=service_id,
            state=HealthState.UNVERIFIED,
            condition=HealthCondition.SOURCE_STALE,
            evaluated_at=now,
            latest_window_start=None,
            latest_window_end=latest_end,
            latest_indicators=None,
            absent_indicators=_absent(None),
            quality=QualityState.STALE,
            sources=sources,
            unknowns=sorted(unknowns) or ["all_observations_stale"],
            limitations=[],
        )

    # Latest complete observation window: all observations sharing the newest
    # window_end, evaluated over the span they cover.
    latest_obs = [o for o in obs if o.window_end == latest_end]
    window = TimeWindow(start=min(o.window_start for o in latest_obs), end=latest_end)
    aggregates = compute_window_aggregates(latest_obs, window, classification, by_operation=False)
    aggregate = aggregates[0] if aggregates else None
    if aggregate is not None:
        limitations.extend(aggregate.limitations)
    absent = _absent(aggregate)

    state = HealthState.UNVERIFIED
    condition = HealthCondition.UNMEASURABLE
    if aggregate is None or aggregate.observations_included == 0:
        unknowns.append("all_observations_excluded")
    elif aggregate.plane is None:
        unknowns.append("no_request_measurements")
    elif aggregate.eligible_requests <= 0:
        condition = HealthCondition.NO_TRAFFIC
        unknowns.append("zero_requests_in_latest_window")
    else:
        ratio = aggregate.error_ratio or 0.0
        if ratio >= policy.unavailable_error_ratio:
            state, condition = HealthState.UNAVAILABLE, HealthCondition.FAILING
        elif ratio > 0.0:
            state, condition = HealthState.PARTIAL, HealthCondition.DEGRADED
        else:
            state, condition = HealthState.AVAILABLE, HealthCondition.OBSERVED

    return ServiceHealthReport(
        service_id=service_id,
        state=state,
        condition=condition,
        evaluated_at=now,
        latest_window_start=window.start,
        latest_window_end=latest_end,
        latest_indicators=aggregate,
        absent_indicators=absent,
        quality=aggregate.quality if aggregate else QualityState.UNKNOWN,
        sources=sources,
        unknowns=sorted(unknowns),
        limitations=sorted(set(limitations)),
    )
