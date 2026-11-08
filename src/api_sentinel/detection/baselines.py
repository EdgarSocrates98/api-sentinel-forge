"""Simple baselines — median/MAD, EWMA, window comparison (docs/08).

Offered *as detector inputs* (`DetectionContext.baselines`), never as root
cause: a baseline describes expected behavior from observed windows and says
nothing about why a deviation happened. Level-1 rules remain threshold
determinism; baselines ride the context for detectors that opt in (Level-2
methods per ADR-007) and are evaluated on a known corpus in tests.

Pure math: deterministic, no I/O, no wall-clock reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from api_sentinel.detection.protocol import MetricBaseline
from api_sentinel.metrics.aggregates import (
    ErrorClassification,
    WindowAggregate,
    compute_window_aggregates,
)
from api_sentinel.metrics.windows import ObservationLike, TimeWindow

_BASELINE_METRICS = ("error_ratio", "latency_p95_ms", "request_rate")


def median(values: Sequence[float]) -> float | None:
    """Median of a numeric series; None for an empty series (never fabricated)."""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: Sequence[float], center: float | None = None) -> float | None:
    """Median absolute deviation around the center (median when omitted)."""
    if not values:
        return None
    c = median(values) if center is None else center
    if c is None:
        return None
    return median([abs(v - c) for v in values])


def ewma(values: Sequence[float], alpha: float = 0.3) -> float | None:
    """Exponentially weighted moving average; newest-first smoothing."""
    if not values or not 0.0 < alpha <= 1.0:
        return None
    level = float(values[0])
    for v in values[1:]:
        level = alpha * v + (1.0 - alpha) * level
    return level


def window_ratio(current: float, reference: float) -> float | None:
    """current/reference for window comparison; None when reference is zero
    (an undefined ratio is reported absent, not coerced to infinity)."""
    if reference == 0:
        return None
    return current / reference


def _window_metrics(aggregate: WindowAggregate) -> dict[str, float]:
    return {k: v for k in _BASELINE_METRICS if (v := getattr(aggregate, k)) is not None}


def summarize_baselines(
    observations: Sequence[ObservationLike],
    *,
    service_id: str,
    before_window_start: datetime,
) -> list[MetricBaseline]:
    """Service-scope baselines from every distinct observation window that
    ends strictly before `before_window_start` (the detection window is never
    part of its own baseline).

    Per window a single service aggregate is computed (sources are never
    summed: the deterministic primary source is taken, as in `evaluate_slo`).
    Each metric's baseline is median ± MAD; a companion `window_ratio`
    baseline carries the newest-window value for comparison inputs.
    """
    scoped = [o for o in observations if o.service_id == service_id]
    windows = sorted({(o.window_start, o.window_end) for o in scoped})
    series: dict[str, list[float]] = {k: [] for k in _BASELINE_METRICS}
    used = 0
    notes: list[str] = []
    for start, end in windows:
        if end >= before_window_start:
            continue
        window = TimeWindow(start=start, end=end)
        aggregates = compute_window_aggregates(
            scoped, window, ErrorClassification(), by_operation=False
        )
        if not aggregates:
            continue
        primary = min(aggregates, key=lambda a: (-a.eligible_requests, a.source_id))
        metrics = _window_metrics(primary)
        if metrics:
            used += 1
            for name, value in metrics.items():
                series[name].append(value)
    if used < 2:
        notes.append(f"thin_baseline:{used}_windows")
    out: list[MetricBaseline] = []
    for name in _BASELINE_METRICS:
        values = series[name]
        if not values:
            out.append(
                MetricBaseline(
                    metric=name,
                    method="median+mad",
                    scope="service",
                    windows_used=used,
                    notes=[*notes, "absent_indicator"],
                )
            )
            continue
        out.append(
            MetricBaseline(
                metric=name,
                method="median+mad",
                scope="service",
                value=median(values),
                deviation=mad(values),
                windows_used=used,
                notes=notes,
            )
        )
        out.append(
            MetricBaseline(
                metric=name,
                method="ewma",
                scope="service",
                value=ewma(values),
                windows_used=used,
                notes=notes,
            )
        )
    return out
