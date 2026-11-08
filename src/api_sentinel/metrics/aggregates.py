"""Windowed request aggregations — the metrics bounded context (docs/05, docs/08).

Pure domain: deterministic, no I/O, no wall-clock reads inside math.

Rules honored (docs/05 fundamental metric rules):

- Percentiles are never averaged — compatible histogram buckets are merged
  first, then quantiles are interpolated inside the containing bucket.
- Counter deltas handle resets: a negative delta over a cumulative counter
  means the process restarted; the post-reset reading is the delta, and a
  rate can never go negative.
- Global rates are never inferred from SAMPLED rows — sampled/invalid
  observations are excluded from counting and reported under
  `excluded_by_reason` plus a limitation (LAB-008).
- Error classification is configurable per service/operation; the default
  is `5xx` + timeout — never a blanket `4xx` (401/403/429 get dedicated
  indicators instead).
- Different sources measuring the same flow are identified, never summed:
  aggregation groups are pinned by `source_id`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from itertools import pairwise
from typing import Any

from pydantic import Field, field_serializer, field_validator

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import MeasurementKind, QualityState, worst_of
from api_sentinel.metrics.windows import BucketLike, ObservationLike, TimeWindow


class RequestClass(StrEnum):
    """How one request-bearing observation classifies for SLI math."""

    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    AUTH_DENIED = "auth_denied"
    RATE_LIMITED = "rate_limited"


class ErrorClassification(FrozenModel):
    """Configurable per-service/operation error definition (docs/05).

    Defaults: HTTP `5xx` and explicit timeout/failure signals are errors.
    `401`/`403`/`429` never count as unavailability unless the config lists
    them (or their class) explicitly — they feed dedicated indicators instead.
    """

    error_status_classes: list[str] = Field(default_factory=lambda: ["5xx"])
    error_status_codes: list[int] = Field(default_factory=list)
    timeout_values: list[str] = Field(
        default_factory=lambda: ["timeout", "deadline_exceeded", "gateway_timeout"]
    )

    @field_validator("error_status_classes")
    @classmethod
    def _classes(cls, v: list[str]) -> list[str]:
        for klass in v:
            if len(klass) != 3 or not klass[0].isdigit() or klass[1:] != "xx":
                raise ValueError(f"invalid status class: {klass!r} (expected e.g. '5xx')")
        return v

    @field_validator("timeout_values")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return [x.strip().lower() for x in v]


_STATUS_ATTR = "http.response.status_code"
_ERROR_ATTRS = ("error.kind", "error.type")

# Labels that pin a cumulative series: two series with different labels never
# difference against each other; a restarted process keeps its labels.
_SERIES_ATTRS = (_STATUS_ATTR, *_ERROR_ATTRS, "metric.name", "sentinel.metric.name")


def _status_code(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _in_class(status: int, klass: str) -> bool:
    """'5xx' matches 500..599 (the model validator guarantees the shape)."""
    base = int(klass[0]) * 100
    return base <= status <= base + 99


def classify_request(attributes: Mapping[str, Any], config: ErrorClassification) -> RequestClass:
    """Classify one observation's request population.

    Precedence: explicit timeout/error attribute -> configured error codes ->
    configured error classes -> dedicated 401/403/429 indicators -> OK.
    """
    error_value = next(
        (str(attributes[k]) for k in _ERROR_ATTRS if attributes.get(k) not in (None, "")),
        None,
    )
    if error_value is not None:
        if error_value.strip().lower() in config.timeout_values:
            return RequestClass.TIMEOUT
        return RequestClass.ERROR
    status = _status_code(attributes.get(_STATUS_ATTR))
    if status is None:
        return RequestClass.OK
    if status in config.error_status_codes:
        return RequestClass.ERROR
    if any(_in_class(status, klass) for klass in config.error_status_classes):
        return RequestClass.ERROR
    if status in (401, 403):
        return RequestClass.AUTH_DENIED
    if status == 429:
        return RequestClass.RATE_LIMITED
    return RequestClass.OK


# ---------------------------------------------------------------------------
# Histogram handling — merge compatible buckets first, quantile after.


class MergedBucket(FrozenModel):
    """One cumulative bucket of a merged distribution (le may be +inf)."""

    le: float  # math.inf for the terminal bucket
    cum_count: float

    @field_serializer("le")
    def _ser_le(self, v: float) -> float | str:
        """'+Inf' in JSON — a bare `Infinity` literal is not standard JSON."""
        return "+Inf" if math.isinf(v) else v


def _le_float(le: float | str) -> float | None:
    if isinstance(le, str):
        return math.inf if le.strip().lower() in {"+inf", "inf", "infinity"} else None
    if isinstance(le, (int, float)) and not isinstance(le, bool):
        return float(le)
    return None


def canonical_cumulative(
    buckets: Sequence[BucketLike],
) -> tuple[list[MergedBucket] | None, str | None]:
    """Return canonical cumulative (le, cum_count) buckets + repair note.

    Disambiguation (recorded, never silent): if the declared cumulative counts
    are non-monotonic *but* the non-terminal counts sum exactly to the terminal
    `+Inf` count, the producer emitted per-bucket counts — they are converted
    to cumulative form and a `bucket_semantics` note is returned. Anything else
    is incompatible and returns (None, reason).
    """
    pairs: list[tuple[float, float]] = []
    for b in buckets:
        bound = _le_float(b.le)
        if bound is None or b.count < 0:
            return None, "bad_bucket"
        pairs.append((bound, float(b.count)))
    pairs.sort(key=lambda p: p[0])
    counts = [c for _, c in pairs]
    if all(a <= b for a, b in pairwise(counts)):
        return [MergedBucket(le=le, cum_count=c) for le, c in pairs], None
    # Non-monotonic cumulative: try the per-bucket reinterpretation.
    if pairs and math.isinf(pairs[-1][0]):
        non_terminal = sum(c for le, c in pairs[:-1])
        if math.isclose(non_terminal, pairs[-1][1]):
            cumulative = 0.0
            out = []
            for le, c in pairs:
                cumulative = pairs[-1][1] if math.isinf(le) else cumulative + c
                out.append(MergedBucket(le=le, cum_count=cumulative))
            return out, "bucket_semantics:per-bucket->cumulative"
    return None, "non_monotonic_buckets"


def _bucket_layout(buckets: Sequence[MergedBucket]) -> tuple[float, ...]:
    return tuple(b.le for b in buckets)


def merge_bucket_sets(
    histograms: Iterable[Sequence[MergedBucket]],
) -> tuple[list[MergedBucket] | None, str | None]:
    """Sum cumulative counts across distributions with identical `le` bounds.

    Compatibility is the exact bound set — no rebucketing, no approximation.
    Returns (merged, incompatibility_reason).
    """
    merged: dict[float, float] = {}
    layout: tuple[float, ...] | None = None
    for buckets in histograms:
        current = _bucket_layout(buckets)
        if layout is None:
            layout = current
        elif current != layout:
            return None, "incompatible_bucket_bounds"
        for b in buckets:
            merged[b.le] = merged.get(b.le, 0.0) + b.cum_count
    if layout is None:
        return None, "no_histograms"
    return [MergedBucket(le=le, cum_count=merged[le]) for le in layout], None


def quantile(buckets: Sequence[MergedBucket], q: float) -> float | None:
    """Prometheus-style linear interpolation inside the containing bucket.

    rank = q·total; the first cumulative bucket covering rank interpolates
    between the previous bound (0 for the first bucket — durations are ≥ 0)
    and its own `le`. The +Inf bucket cannot interpolate upward — the last
    finite bound is returned. Percentiles are computed only over merged
    distributions, never averaged (docs/05 invariant).
    """
    if not buckets or not (0.0 <= q <= 1.0):
        return None
    total = buckets[-1].cum_count
    if total <= 0:
        return None
    rank = q * total
    prev_le = 0.0
    prev_cum = 0.0
    for b in buckets:
        if b.cum_count >= rank:
            if math.isinf(b.le):
                return prev_le
            count_in_bucket = b.cum_count - prev_cum
            if count_in_bucket <= 0:
                return b.le
            fraction = (rank - prev_cum) / count_in_bucket
            return prev_le + (b.le - prev_le) * fraction
        prev_le = b.le
        prev_cum = b.cum_count
    return prev_le


def cumulative_at(buckets: Sequence[MergedBucket], x: float) -> float:
    """Interpolated cumulative count at bound `x` (inverse of `quantile`).

    Used by the latency SLI: `within threshold / eligible` (docs/08) needs the
    estimated count of observations ≤ threshold inside its containing bucket.
    """
    prev_le = 0.0
    prev_cum = 0.0
    for b in buckets:
        if x <= b.le:
            if math.isinf(b.le):
                return b.cum_count
            count_in_bucket = b.cum_count - prev_cum
            if count_in_bucket <= 0:
                return b.cum_count
            fraction = (x - prev_le) / (b.le - prev_le)
            return prev_cum + count_in_bucket * fraction
        prev_le = b.le
        prev_cum = b.cum_count
    return prev_cum


# ---------------------------------------------------------------------------
# Counter handling — delta/cumulative temporality, reset-safe deltas.


def counter_deltas(readings: Sequence[tuple[datetime, float]]) -> list[float]:
    """Deltas over cumulative readings sorted by time; resets never go negative.

    A negative delta means the counter reset (process restart) — the delta for
    that step is the post-reset reading itself. The first reading of a series
    has no predecessor and yields no delta (it carries pre-window history).
    """
    out: list[float] = []
    previous: float | None = None
    for _, value in sorted(readings, key=lambda r: r[0]):
        if previous is None:
            previous = value
            continue
        delta = value - previous
        out.append(value if delta < 0 else delta)
        previous = value
    return out


def _series_key(obs: ObservationLike) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, str(obs.attributes[k])) for k in _SERIES_ATTRS if k in obs.attributes))


# ---------------------------------------------------------------------------
# The aggregate itself.

_REQUEST_COUNTER_UNITS = {"requests", "count"}
# Quality states that must not feed global-rate math (LAB-008, invariants).
_EXCLUDED_QUALITY = {QualityState.SAMPLED: "sampled", QualityState.INVALID: "invalid"}
_DURATION_UNIT = "ms"
_PLANE_PRECEDENCE = ("histogram", "counter", "summary")


class WindowAggregate(FrozenModel):
    """Windowed request/latency/error indicators for one (service, operation,
    source) population — the unit of SLI evaluation.

    `None` indicators are absent — the source cannot supply them and they are
    never interpolated (docs/08).
    """

    service_id: str
    operation_id: str | None
    source_id: str
    window_start: datetime
    window_end: datetime
    plane: str | None  # histogram | counter | summary | None (no request data)
    eligible_requests: float = 0.0
    ok_requests: float = 0.0  # strictly OK — excludes dedicated 401/403/429 classes
    error_requests: float = 0.0
    timeout_requests: float = 0.0
    auth_denied_requests: float = 0.0
    rate_limited_requests: float = 0.0
    request_rate: float | None = None
    error_ratio: float | None = None
    timeout_ratio: float | None = None
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    latency_p99_ms: float | None = None
    latency_mean_ms: float | None = None
    latency_buckets: list[MergedBucket] = Field(default_factory=list)
    quality: QualityState = QualityState.UNKNOWN
    observations_included: int = 0
    observations_excluded: int = 0
    excluded_by_reason: dict[str, int] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


def _series_deltas(
    observations: Sequence[ObservationLike],
) -> tuple[list[tuple[ObservationLike, float]], list[str]]:
    """Per-observation request deltas for cumulative counters/histograms.

    Returns (observation, requests) pairs + limitations. A series' first
    reading has no predecessor: its value carries pre-window history and
    contributes no delta (`counter_baseline_missing`, recorded once).
    """
    by_series: dict[tuple[tuple[str, str], ...], list[ObservationLike]] = {}
    for obs in observations:
        by_series.setdefault(_series_key(obs), []).append(obs)
    pairs: list[tuple[ObservationLike, float]] = []
    limitations: list[str] = []
    for series_obs in by_series.values():
        ordered = sorted(series_obs, key=lambda o: o.window_end)
        previous: ObservationLike | None = None
        for obs in ordered:
            if previous is None:
                previous = obs
                limitations.append("counter_baseline_missing")
                continue
            if obs.measurement.kind == MeasurementKind.COUNTER:
                current = obs.measurement.value or 0.0
                raw = current - (previous.measurement.value or 0.0)
                delta = current if raw < 0 else raw
            else:  # histogram/summary: difference the totals
                current_count = float(obs.measurement.count or 0)
                raw = current_count - float(previous.measurement.count or 0)
                delta = current_count if raw < 0 else raw
            pairs.append((obs, delta))
            previous = obs
    return pairs, sorted(set(limitations))


def _request_contributions(
    plane_obs: Sequence[ObservationLike],
) -> tuple[list[tuple[ObservationLike, float]], list[str]]:
    """(observation, request_count) pairs for the chosen measurement plane.

    Delta temporality (or absent) counts each observation's own value.
    Cumulative temporality is differenced per series — resets never yield
    negative rates (invariant).
    """
    pairs: list[tuple[ObservationLike, float]] = []
    limitations: list[str] = []
    cumulative: list[ObservationLike] = []
    for obs in plane_obs:
        if obs.measurement.temporality == "cumulative":
            cumulative.append(obs)
        elif obs.measurement.kind == MeasurementKind.COUNTER:
            pairs.append((obs, obs.measurement.value or 0.0))
        else:
            pairs.append((obs, float(obs.measurement.count or 0)))
    if cumulative:
        cumulative_pairs, cumulative_limits = _series_deltas(cumulative)
        pairs.extend(cumulative_pairs)
        limitations.extend(cumulative_limits)
    return pairs, limitations


def _merged_latency_distribution(
    plane_obs: Sequence[ObservationLike],
) -> tuple[list[MergedBucket], float | None, float, list[str]]:
    """Merged cumulative buckets over duration histograms (canonical ms).

    Returns (merged buckets, total sum for the mean, merged count, notes).
    Cumulative points are differenced per series first; only identical bound
    sets merge — anything else reports `incompatible_bucket_bounds` instead of
    rebucketing. Notes cover both repairs and limitations; callers record them.
    """
    delta_histograms: list[list[MergedBucket]] = []
    cumulative_by_series: dict[tuple[tuple[str, str], ...], list[ObservationLike]] = {}
    canonical: dict[int, list[MergedBucket]] = {}
    notes: list[str] = []
    total_sum = 0.0
    sums_seen = 0
    cumulative_seen = False

    for obs in plane_obs:
        if obs.measurement.kind != MeasurementKind.HISTOGRAM:
            continue
        if obs.measurement.unit != _DURATION_UNIT:
            notes.append("non_duration_histogram_ignored")
            continue
        buckets, note = canonical_cumulative(obs.measurement.buckets)
        if buckets is None:
            notes.append(f"incompatible_histogram:{note}")
            continue
        if note is not None:
            notes.append(note)
        canonical[id(obs)] = buckets
        if obs.measurement.temporality == "cumulative":
            cumulative_seen = True
            cumulative_by_series.setdefault(_series_key(obs), []).append(obs)
        else:
            delta_histograms.append(buckets)
            if obs.measurement.sum is not None:
                total_sum += obs.measurement.sum
                sums_seen += 1

    for series_obs in cumulative_by_series.values():
        ordered = sorted(series_obs, key=lambda o: o.window_end)
        notes.append("counter_baseline_missing")  # first reading yields no delta
        for prev, curr in pairwise(ordered):
            prev_b = canonical[id(prev)]
            curr_b = canonical[id(curr)]
            if _bucket_layout(prev_b) != _bucket_layout(curr_b):
                notes.append("incompatible_bucket_bounds")
                continue
            reset = any(c.cum_count < p.cum_count for p, c in zip(prev_b, curr_b, strict=True))
            delta_histograms.append(
                [
                    MergedBucket(
                        le=c.le,
                        cum_count=c.cum_count if reset else c.cum_count - p.cum_count,
                    )
                    for p, c in zip(prev_b, curr_b, strict=True)
                ]
            )
    if cumulative_seen:
        notes.append("latency_mean_unavailable:cumulative_temporality")

    merged, reason = merge_bucket_sets(delta_histograms)
    if merged is None:
        if reason is not None and reason != "no_histograms":
            notes.append(reason)
        return [], (total_sum if sums_seen else None), 0.0, notes
    return (
        merged,
        total_sum if (sums_seen and not cumulative_seen) else None,
        merged[-1].cum_count,
        notes,
    )


def compute_window_aggregates(
    observations: Iterable[ObservationLike],
    window: TimeWindow,
    classification: ErrorClassification | None = None,
    *,
    by_operation: bool = True,
) -> list[WindowAggregate]:
    """Compute per-(service, operation, source) indicators over `window`.

    Only observations fully contained in the window contribute. SAMPLED and
    INVALID rows are excluded from rate math and reported under
    `excluded_by_reason` — global rates are never inferred from them.
    """
    config = classification or ErrorClassification()
    groups: dict[tuple[str, str | None, str], list[ObservationLike]] = {}
    for obs in observations:
        if not window.contains(obs):
            continue
        operation = obs.operation_id if by_operation else None
        groups.setdefault((obs.service_id, operation, obs.source_id), []).append(obs)

    aggregates: list[WindowAggregate] = []
    for (service_id, operation_id, source_id), group in sorted(groups.items()):
        excluded: dict[str, int] = {}
        usable: list[ObservationLike] = []
        for obs in group:
            reason = _EXCLUDED_QUALITY.get(obs.quality)
            if reason is None:
                usable.append(obs)
            else:
                excluded[reason] = excluded.get(reason, 0) + 1

        # The request plane: one measurement kind per population. When a source
        # supplies both a request counter and a duration histogram, the
        # histogram is authoritative (richer distribution) — never summed.
        plane_obs_by_kind: dict[str, list[ObservationLike]] = {k: [] for k in _PLANE_PRECEDENCE}
        for obs in usable:
            kind = obs.measurement.kind
            unit = obs.measurement.unit
            if kind == MeasurementKind.HISTOGRAM and unit == _DURATION_UNIT:
                plane_obs_by_kind["histogram"].append(obs)
            elif kind == MeasurementKind.COUNTER and (
                unit is None or unit in _REQUEST_COUNTER_UNITS
            ):
                plane_obs_by_kind["counter"].append(obs)
            elif kind == MeasurementKind.SUMMARY:
                plane_obs_by_kind["summary"].append(obs)
        plane = next((k for k in _PLANE_PRECEDENCE if plane_obs_by_kind[k]), None)
        plane_obs = plane_obs_by_kind[plane] if plane else []

        limitations: list[str] = []
        present_planes = [k for k in _PLANE_PRECEDENCE if plane_obs_by_kind[k]]
        if len(present_planes) > 1:
            limitations.append(f"multiple_request_planes:{','.join(present_planes)}:using {plane}")
        if plane is None and usable:
            limitations.append("no_request_plane")

        contributions, contribution_limits = _request_contributions(plane_obs)
        limitations.extend(contribution_limits)

        eligible = 0.0
        per_class: dict[RequestClass, float] = {}
        for obs, requests in contributions:
            eligible += requests
            klass = classify_request(obs.attributes, config)
            per_class[klass] = per_class.get(klass, 0.0) + requests
        errors = per_class.get(RequestClass.ERROR, 0.0)
        timeouts = per_class.get(RequestClass.TIMEOUT, 0.0)
        auth_denied = per_class.get(RequestClass.AUTH_DENIED, 0.0)
        rate_limited = per_class.get(RequestClass.RATE_LIMITED, 0.0)
        bad = errors + timeouts
        ok = eligible - bad - auth_denied - rate_limited

        buckets, latency_sum, merged_count, latency_notes = _merged_latency_distribution(plane_obs)
        limitations.extend(latency_notes)
        if excluded:
            limitations.append(
                "excluded_observations:" + ",".join(f"{k}={v}" for k, v in sorted(excluded.items()))
            )

        seconds = window.seconds
        aggregates.append(
            WindowAggregate(
                service_id=service_id,
                operation_id=operation_id,
                source_id=source_id,
                window_start=window.start,
                window_end=window.end,
                plane=plane,
                eligible_requests=eligible,
                ok_requests=ok,
                error_requests=errors,
                timeout_requests=timeouts,
                auth_denied_requests=auth_denied,
                rate_limited_requests=rate_limited,
                request_rate=(eligible / seconds) if plane and seconds > 0 else None,
                error_ratio=(bad / eligible) if eligible > 0 else None,
                timeout_ratio=(timeouts / eligible) if eligible > 0 else None,
                latency_p50_ms=quantile(buckets, 0.50) if buckets else None,
                latency_p95_ms=quantile(buckets, 0.95) if buckets else None,
                latency_p99_ms=quantile(buckets, 0.99) if buckets else None,
                latency_mean_ms=(
                    latency_sum / merged_count
                    if latency_sum is not None and merged_count > 0
                    else None
                ),
                latency_buckets=buckets,
                quality=worst_of([o.quality for o in usable]),
                observations_included=len(usable),
                observations_excluded=sum(excluded.values()),
                excluded_by_reason=excluded,
                limitations=sorted(set(limitations)),
            )
        )
    return aggregates
