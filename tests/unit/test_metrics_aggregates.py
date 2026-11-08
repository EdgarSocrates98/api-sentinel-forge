"""Unit oracles for metrics: resets, sampling, merged-bucket percentiles.

Expected values are hand-computed — tests prove math, not implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api_sentinel.domain.quality import MeasurementKind, QualityState
from api_sentinel.ingestion.models import (
    NormalizedMeasurement,
    NormalizedObservation,
    ObsBucket,
)
from api_sentinel.metrics import (
    ErrorClassification,
    MergedBucket,
    RequestClass,
    TimeWindow,
    classify_request,
    compute_window_aggregates,
    counter_deltas,
    parse_duration_seconds,
    quantile,
)

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
WINDOW = TimeWindow(start=T0, end=T0 + timedelta(minutes=5))


def _t(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _obs(
    *,
    obs_id: str = "o1",
    service: str = "svc_1",
    operation: str | None = "op_1",
    source: str = "src_1",
    start_min: float = 0.0,
    duration_min: float = 1.0,
    quality: QualityState = QualityState.COMPLETE,
    kind: MeasurementKind = MeasurementKind.COUNTER,
    unit: str | None = "requests",
    value: float | None = 10.0,
    count: int | None = None,
    buckets: list[tuple[float | str, int]] | None = None,
    temporality: str | None = None,
    attributes: dict | None = None,
) -> NormalizedObservation:
    start = _t(start_min)
    return NormalizedObservation(
        observation_id=obs_id,
        service_id=service,
        operation_id=operation,
        release_id=None,
        window_start=start,
        window_end=start + timedelta(minutes=duration_min),
        ingest_time=T0,
        processing_time=T0,
        source_id=source,
        batch_id="b1",
        quality=quality,
        measurement=NormalizedMeasurement(
            kind=kind,
            unit=unit,
            value=value,
            buckets=[ObsBucket(le=le, count=c) for le, c in (buckets or [])],
            count=count,
            temporality=temporality,
        ),
        attributes=attributes or {},
        idempotency_key=f"k-{obs_id}",
    )


def _counter(obs_id: str, start_min: float, value: float, **kw) -> NormalizedObservation:
    return _obs(obs_id=obs_id, start_min=start_min, value=value, **kw)


# --- counters ---------------------------------------------------------------


def test_delta_counters_sum_to_eligible() -> None:
    obs = [_counter(f"o{i}", i, 10.0) for i in range(3)]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.plane == "counter"
    assert agg.eligible_requests == 30.0
    assert agg.request_rate == pytest.approx(30.0 / 300.0)
    assert agg.error_ratio == 0.0


def test_cumulative_counter_reset_never_negative() -> None:
    # Series: 100 -> 150 -> 40 (reset) -> 90. Deltas: 50, 40, 50 = 140.
    readings = [
        _counter(f"o{i}", i, v, temporality="cumulative")
        for i, v in enumerate([100.0, 150.0, 40.0, 90.0])
    ]
    (agg,) = compute_window_aggregates(readings, WINDOW)
    assert agg.eligible_requests == pytest.approx(140.0)
    assert all(v >= 0 for v in counter_deltas([(_t(i), v) for i, v in enumerate([5, 2, 9])]))
    assert "counter_baseline_missing" in agg.limitations


def test_counter_deltas_oracle() -> None:
    deltas = counter_deltas([(T0, 0.0), (_t(1), 7.0), (_t(2), 3.0), (_t(3), 12.0)])
    assert deltas == [7.0, 3.0, 9.0]  # 7-0, reset->3, 12-3


def test_sampled_and_invalid_never_feed_global_rates() -> None:
    obs = [
        _counter("sampled", 0, 500.0, quality=QualityState.SAMPLED),
        _counter("invalid", 1, 700.0, quality=QualityState.INVALID),
        _counter("clean", 2, 100.0),
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.eligible_requests == 100.0  # LAB-008: no rates inferred from sampled
    assert agg.excluded_by_reason == {"invalid": 1, "sampled": 1}
    assert agg.observations_excluded == 2
    assert any("excluded_observations" in lim for lim in agg.limitations)


# --- error classification ---------------------------------------------------


def test_classification_defaults_not_blanket_4xx() -> None:
    cfg = ErrorClassification()
    assert classify_request({"http.response.status_code": 200}, cfg) is RequestClass.OK
    assert classify_request({"http.response.status_code": 404}, cfg) is RequestClass.OK
    assert classify_request({"http.response.status_code": 500}, cfg) is RequestClass.ERROR
    assert classify_request({"http.response.status_code": "503"}, cfg) is RequestClass.ERROR
    assert classify_request({"http.response.status_code": 401}, cfg) is RequestClass.AUTH_DENIED
    assert classify_request({"http.response.status_code": 403}, cfg) is RequestClass.AUTH_DENIED
    assert classify_request({"http.response.status_code": 429}, cfg) is RequestClass.RATE_LIMITED
    assert classify_request({"error.kind": "timeout"}, cfg) is RequestClass.TIMEOUT
    assert classify_request({"error.type": "deadline_exceeded"}, cfg) is RequestClass.TIMEOUT
    assert classify_request({"error.kind": "crash"}, cfg) is RequestClass.ERROR


def test_classification_is_configurable() -> None:
    promote = ErrorClassification(error_status_codes=[429])
    assert classify_request({"http.response.status_code": 429}, promote) is RequestClass.ERROR
    blanket = ErrorClassification(error_status_classes=["4xx", "5xx"])
    assert classify_request({"http.response.status_code": 404}, blanket) is RequestClass.ERROR
    assert classify_request({"http.response.status_code": 401}, blanket) is RequestClass.ERROR


def test_dedicated_indicators_counted_not_bad() -> None:
    obs = [
        _counter("ok", 0, 90.0, attributes={"http.response.status_code": 200}),
        _counter("denied", 1, 6.0, attributes={"http.response.status_code": 401}),
        _counter("limited", 2, 4.0, attributes={"http.response.status_code": 429}),
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.eligible_requests == 100.0
    assert agg.auth_denied_requests == 6.0
    assert agg.rate_limited_requests == 4.0
    assert agg.error_requests == 0.0
    assert agg.error_ratio == 0.0  # dedicated classes are not unavailability
    assert agg.ok_requests == 90.0


def test_mixed_classification_aggregation() -> None:
    obs = [
        _counter("ok", 0, 80.0, attributes={"http.response.status_code": 200}),
        _counter("err", 1, 15.0, attributes={"http.response.status_code": 500}),
        _counter("tmo", 2, 5.0, attributes={"error.kind": "timeout"}),
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.eligible_requests == 100.0
    assert agg.error_requests == 15.0
    assert agg.timeout_requests == 5.0
    assert agg.error_ratio == pytest.approx(0.20)
    assert agg.timeout_ratio == pytest.approx(0.05)


# --- histograms -------------------------------------------------------------


def _hist(obs_id: str, start_min: float, cum: list[tuple[float | str, int]], **kw):
    return _obs(
        obs_id=obs_id,
        start_min=start_min,
        kind=MeasurementKind.HISTOGRAM,
        unit="ms",
        value=None,
        count=int(cum[-1][1]),
        buckets=cum,
        **kw,
    )


def test_percentile_from_merged_buckets_oracle() -> None:
    # h1 cumulative [10,30,40,40], h2 cumulative [5,15,25,25]
    # merged [15,45,65,65] total 65 — percentiles from the merged distribution,
    # never the average of per-histogram percentiles.
    obs = [
        _hist("h1", 0, [(50, 10), (100, 30), (200, 40), ("+Inf", 40)]),
        _hist("h2", 1, [(50, 5), (100, 15), (200, 25), ("+Inf", 25)]),
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.plane == "histogram"
    assert agg.eligible_requests == 65.0
    # p50: rank 32.5 in (50,100]: 50 + 50*(32.5-15)/30
    assert agg.latency_p50_ms == pytest.approx(50 + 50 * 17.5 / 30)
    # p95: rank 61.75 in (100,200]: 100 + 100*(61.75-45)/20
    assert agg.latency_p95_ms == pytest.approx(100 + 100 * 16.75 / 20)
    # p99: rank 64.35 in (100,200]: 100 + 100*(64.35-45)/20
    assert agg.latency_p99_ms == pytest.approx(100 + 100 * 19.35 / 20)


def test_quantile_plus_inf_bound_returns_last_finite() -> None:
    # 20 obs: 10 <= 50, 0 in (50,100], 10 in (100,+Inf]. p95 rank=19 lands in
    # the +Inf bucket — cannot interpolate upward, returns last finite bound.
    buckets = [
        MergedBucket(le=50.0, cum_count=10.0),
        MergedBucket(le=100.0, cum_count=10.0),
        MergedBucket(le=float("inf"), cum_count=20.0),
    ]
    assert quantile(buckets, 0.95) == 100.0
    # p50 rank=10 lands on the le=50 edge.
    assert quantile(buckets, 0.5) == 50.0


def test_per_bucket_semantics_repair_is_recorded() -> None:
    # Producer emitted per-bucket counts (non-monotonic cumulative) but the
    # +Inf total disambiguates: 10+55+40+12+3 == 120.
    obs = [
        _hist("h", 0, [(50, 10), (100, 55), (200, 40), (500, 12), (1000, 3), ("+Inf", 120)])
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.latency_buckets  # repaired into cumulative form
    assert [b.cum_count for b in agg.latency_buckets] == [10, 65, 105, 117, 120, 120]
    assert any("bucket_semantics" in lim for lim in agg.limitations)


def test_incompatible_bounds_never_rebucketed() -> None:
    obs = [
        _hist("h1", 0, [(50, 10), ("+Inf", 10)]),
        _hist("h2", 1, [(75, 5), ("+Inf", 5)]),
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.latency_p50_ms is None
    assert any("incompatible_bucket_bounds" in lim for lim in agg.limitations)


def test_cumulative_histograms_differenced_per_series() -> None:
    # Cumulative series: t0 [5,10] -> t1 [10,25] -> t2 [12,30].
    # Delta t1 = [5,15], delta t2 = [2,5] -> merged [7,20] total 20.
    obs = [
        _hist("h0", 0, [(100, 5), ("+Inf", 10)], temporality="cumulative"),
        _hist("h1", 1, [(100, 10), ("+Inf", 25)], temporality="cumulative"),
        _hist("h2", 2, [(100, 12), ("+Inf", 30)], temporality="cumulative"),
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.eligible_requests == pytest.approx(20.0)
    assert [b.cum_count for b in agg.latency_buckets] == [7.0, 20.0]


def test_histograms_and_counters_share_no_plane() -> None:
    # Counter + histogram for the same flow: histogram is authoritative,
    # the counter is never double-counted — the mixing is reported.
    obs = [
        _counter("c", 0, 60.0),
        _hist("h", 1, [(100, 10), ("+Inf", 10)]),
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.plane == "histogram"
    assert agg.eligible_requests == 10.0
    assert any("multiple_request_planes" in lim for lim in agg.limitations)


# --- grouping, containment, edge cases --------------------------------------


def test_sources_identified_never_summed() -> None:
    obs = [
        _counter("a", 0, 100.0, source="src_a"),
        _counter("b", 1, 100.0, source="src_b"),
    ]
    aggs = compute_window_aggregates(obs, WINDOW)
    assert {a.source_id for a in aggs} == {"src_a", "src_b"}
    assert all(a.eligible_requests == 100.0 for a in aggs)


def test_operations_grouped_separately() -> None:
    obs = [
        _counter("a", 0, 10.0, operation="op_a"),
        _counter("b", 1, 20.0, operation="op_b"),
    ]
    aggs = compute_window_aggregates(obs, WINDOW)
    assert {a.operation_id for a in aggs} == {"op_a", "op_b"}
    (merged,) = compute_window_aggregates(obs, WINDOW, by_operation=False)
    assert merged.operation_id is None and merged.eligible_requests == 30.0


def test_observation_outside_window_not_counted() -> None:
    obs = [
        _counter("in", 0, 10.0),
        _counter("out", 10, 999.0),  # 00:10-00:11, outside [00:00, 00:05)
        _obs(obs_id="partial", start_min=4, duration_min=3, value=50.0),  # spills out
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.eligible_requests == 10.0


def test_no_request_plane_reports_absent() -> None:
    obs = [
        _obs(
            obs_id="g",
            start_min=0,
            kind=MeasurementKind.GAUGE,
            unit="count",
            value=4.0,
        )
    ]
    (agg,) = compute_window_aggregates(obs, WINDOW)
    assert agg.plane is None
    assert agg.eligible_requests == 0.0
    assert agg.request_rate is None and agg.error_ratio is None
    assert "no_request_plane" in agg.limitations


def test_parse_duration_seconds() -> None:
    assert parse_duration_seconds("60s") == 60
    assert parse_duration_seconds("15m") == 900
    assert parse_duration_seconds("1h") == 3600
    assert parse_duration_seconds("7d") == 604800
    with pytest.raises(ValueError, match="invalid duration"):
        parse_duration_seconds("abc")
