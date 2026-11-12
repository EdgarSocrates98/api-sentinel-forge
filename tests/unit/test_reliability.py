"""Unit oracles for reliability: SLI/budget math, versioned SLOs, health states.

Expected values are hand-computed from docs/08 formulas:
SLI = good/eligible; B = N(1-S); consumed = bad / N(1-S).
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
from api_sentinel.metrics import ErrorClassification, TimeWindow
from api_sentinel.reliability import (
    AssessmentState,
    HealthCondition,
    HealthState,
    SliKind,
    SloDefinition,
    SloThresholds,
    assess_service_health,
    assessment_id_for,
    budget_consumed,
    budget_remaining,
    error_budget,
    evaluate_slo,
    sli_value,
)
from api_sentinel.reliability.slo import RULE_ID, RULE_VERSION

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
NOW = T0 + timedelta(minutes=10)
WINDOW = TimeWindow(start=T0, end=T0 + timedelta(minutes=5))


def _t(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _obs(
    *,
    obs_id: str = "o1",
    service: str = "svc_1",
    operation: str | None = None,
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


def _definition(**kw) -> SloDefinition:
    params = {
        "slo_id": "slo_1",
        "revision": 1,
        "service_id": "svc_1",
        "sli_kind": SliKind.AVAILABILITY,
        "target": 0.99,
        "window_seconds": 300,
        "created_at": T0,
    }
    params.update(kw)
    return SloDefinition(**params)


# --- SLI / budget math oracles -----------------------------------------------


def test_sli_undefined_when_no_eligible() -> None:
    assert sli_value(0.0, 0.0) is None  # never fabricate 1.0
    assert sli_value(99.0, 100.0) == pytest.approx(0.99)


def test_error_budget_oracle() -> None:
    # B = N(1-S): 1000 eligible at S=0.99 -> 10 requests of budget.
    assert error_budget(1000.0, 0.99) == pytest.approx(10.0)
    # consumed = bad / N(1-S): 10 bad -> 1.0 (budget gone), 20 bad -> 2.0.
    assert budget_consumed(10.0, 1000.0, 0.99) == pytest.approx(1.0)
    assert budget_consumed(20.0, 1000.0, 0.99) == pytest.approx(2.0)
    assert budget_remaining(20.0, 1000.0, 0.99) == pytest.approx(-10.0)
    assert budget_consumed(0.0, 0.0, 0.99) is None  # zero denominator is reported


def test_availability_assessment_oracle_met() -> None:
    # 990 ok + 10 error over 1000 eligible -> SLI 0.99, exactly the target.
    obs = [
        _counter("ok", 0, 990.0, attributes={"http.response.status_code": 200}),
        _counter("err", 1, 10.0, attributes={"http.response.status_code": 500}),
    ]
    asmt = evaluate_slo(definition=_definition(), observations=obs, window=WINDOW, evaluated_at=NOW)
    assert asmt.state is AssessmentState.MET
    assert asmt.sli == pytest.approx(0.99)
    assert asmt.eligible == 1000.0 and asmt.good == 990.0 and asmt.bad == 10.0
    assert asmt.budget_total == pytest.approx(10.0)
    assert asmt.budget_consumed == pytest.approx(1.0)
    assert asmt.budget_remaining == pytest.approx(0.0)
    assert asmt.rule_id == RULE_ID and asmt.rule_version == RULE_VERSION
    assert asmt.quality is QualityState.COMPLETE


def test_availability_assessment_oracle_violated() -> None:
    # Same data, target 0.995 -> violated; consumed = 10 / (1000*0.005) = 2.0.
    obs = [
        _counter("ok", 0, 990.0, attributes={"http.response.status_code": 200}),
        _counter("err", 1, 10.0, attributes={"http.response.status_code": 500}),
    ]
    asmt = evaluate_slo(
        definition=_definition(target=0.995),
        observations=obs,
        window=WINDOW,
        evaluated_at=NOW,
    )
    assert asmt.state is AssessmentState.VIOLATED
    assert asmt.sli == pytest.approx(0.99)
    assert asmt.budget_consumed == pytest.approx(2.0)


def test_simulated_source_provenance_is_labeled_not_promoted() -> None:
    # C101-F009: a MET computed on simulated traffic must be *labeled* —
    # simulated data can never masquerade as real production health.
    obs = [
        _counter("ok", 0, 990.0, source="sim_1", attributes={"http.response.status_code": 200}),
        _counter("err", 1, 10.0, source="sim_1", attributes={"http.response.status_code": 500}),
    ]
    asmt = evaluate_slo(
        definition=_definition(),
        observations=obs,
        window=WINDOW,
        evaluated_at=NOW,
        source_kinds={"sim_1": "simulated"},
    )
    # Math is unchanged — the provenance label is what changed.
    assert asmt.state is AssessmentState.MET
    assert asmt.primary_source_id == "sim_1"
    assert asmt.per_source[0].source_kind == "simulated"
    assert "simulated_source_data:sim_1" in asmt.limitations
    assert "simulated_primary_source" in asmt.limitations


def test_unknown_source_kind_leaves_assessment_unchanged() -> None:
    obs = [_counter("ok", 0, 100.0, attributes={"http.response.status_code": 200})]
    asmt = evaluate_slo(
        definition=_definition(),
        observations=obs,
        window=WINDOW,
        evaluated_at=NOW,
        source_kinds={"src_1": "collector"},
    )
    assert asmt.state is AssessmentState.MET
    assert asmt.per_source[0].source_kind == "collector"
    assert not [lim for lim in asmt.limitations if lim.startswith("simulated")]


def test_dedicated_indicators_do_not_consume_budget() -> None:
    # 401/403/429 are dedicated indicators, not unavailability (docs/05).
    obs = [
        _counter("ok", 0, 90.0, attributes={"http.response.status_code": 200}),
        _counter("denied", 1, 6.0, attributes={"http.response.status_code": 403}),
        _counter("limited", 2, 4.0, attributes={"http.response.status_code": 429}),
    ]
    asmt = evaluate_slo(definition=_definition(), observations=obs, window=WINDOW, evaluated_at=NOW)
    assert asmt.state is AssessmentState.MET
    assert asmt.sli == pytest.approx(1.0)
    assert asmt.bad == 0.0
    # ...unless the definition promotes them to errors.
    strict = _definition(
        error_classification=ErrorClassification(
            error_status_classes=["5xx"], error_status_codes=[429]
        )
    )
    promoted = evaluate_slo(definition=strict, observations=obs, window=WINDOW, evaluated_at=NOW)
    assert promoted.bad == 4.0
    assert promoted.sli == pytest.approx(0.96)


def test_latency_sli_oracle() -> None:
    # Merged distribution [50:10, 100:20, +Inf:20] (cumulative); threshold 75ms
    # -> within = 10 + 10*(75-50)/(100-50) = 15 -> SLI 15/20 = 0.75.
    obs = [_hist("h", 0, [(50, 10), (100, 20), ("+Inf", 20)])]
    definition = _definition(
        sli_kind=SliKind.LATENCY,
        thresholds=SloThresholds(latency_ms=75.0),
        target=0.5,
    )
    asmt = evaluate_slo(definition=definition, observations=obs, window=WINDOW, evaluated_at=NOW)
    assert asmt.sli == pytest.approx(0.75)
    assert asmt.good == pytest.approx(15.0)
    assert asmt.bad == pytest.approx(5.0)
    assert asmt.state is AssessmentState.MET


def test_latency_sli_without_distribution_is_insufficient() -> None:
    obs = [_counter("c", 0, 100.0)]
    definition = _definition(
        sli_kind=SliKind.LATENCY,
        thresholds=SloThresholds(latency_ms=75.0),
        target=0.5,
    )
    asmt = evaluate_slo(definition=definition, observations=obs, window=WINDOW, evaluated_at=NOW)
    assert asmt.state is AssessmentState.INSUFFICIENT_DATA
    assert asmt.sli is None
    assert "no_latency_distribution" in asmt.limitations


# --- states, determinism, coverage -------------------------------------------


def test_insufficient_when_no_observations() -> None:
    asmt = evaluate_slo(definition=_definition(), observations=[], window=WINDOW, evaluated_at=NOW)
    assert asmt.state is AssessmentState.INSUFFICIENT_DATA
    assert asmt.sli is None
    assert "no_observations_in_window" in asmt.limitations
    assert asmt.coverage.observations_included == 0


def test_incompatible_when_no_request_plane() -> None:
    obs = [
        _obs(
            obs_id="g",
            start_min=0,
            kind=MeasurementKind.GAUGE,
            unit="count",
            value=3.0,
        )
    ]
    asmt = evaluate_slo(definition=_definition(), observations=obs, window=WINDOW, evaluated_at=NOW)
    assert asmt.state is AssessmentState.INCOMPATIBLE_INPUTS
    assert "no_shared_request_population" in asmt.limitations


def test_all_sampled_is_insufficient_not_healthy() -> None:
    obs = [
        _counter("s", 0, 500.0, quality=QualityState.SAMPLED),
        _counter("s2", 1, 700.0, quality=QualityState.SAMPLED),
    ]
    asmt = evaluate_slo(definition=_definition(), observations=obs, window=WINDOW, evaluated_at=NOW)
    assert asmt.state is AssessmentState.INSUFFICIENT_DATA
    assert asmt.sli is None
    assert "all_observations_excluded" in asmt.limitations
    assert asmt.coverage.excluded_by_reason == {"sampled": 2}


def test_multiple_sources_identified_not_summed() -> None:
    obs = [
        _counter("a", 0, 1000.0, source="src_a"),
        _counter("b", 1, 10.0, source="src_b", attributes={"http.response.status_code": 500}),
    ]
    asmt = evaluate_slo(definition=_definition(), observations=obs, window=WINDOW, evaluated_at=NOW)
    assert len(asmt.per_source) == 2
    assert asmt.primary_source_id == "src_a"  # largest eligible population
    assert asmt.eligible == 1000.0  # not 1010 — sources never sum
    assert any("multiple_sources_not_summed" in lim for lim in asmt.limitations)


def test_evaluation_is_reproducible() -> None:
    obs = [
        _counter("ok", 0, 990.0, attributes={"http.response.status_code": 200}),
        _counter("err", 1, 10.0, attributes={"http.response.status_code": 500}),
    ]
    a = evaluate_slo(definition=_definition(), observations=obs, window=WINDOW, evaluated_at=NOW)
    b = evaluate_slo(
        definition=_definition(), observations=list(obs), window=WINDOW, evaluated_at=NOW
    )
    assert a == b
    assert a.assessment_id == assessment_id_for("slo_1", 1, WINDOW)
    assert a.assessment_id != assessment_id_for("slo_1", 2, WINDOW)


def test_operation_scoped_slo() -> None:
    obs = [
        _counter("a", 0, 100.0, operation="op_a", attributes={"http.response.status_code": 500}),
        _counter("b", 1, 100.0, operation="op_b"),
    ]
    asmt = evaluate_slo(
        definition=_definition(operation_id="op_b"),
        observations=obs,
        window=WINDOW,
        evaluated_at=NOW,
    )
    assert asmt.eligible == 100.0 and asmt.sli == pytest.approx(1.0)


# --- health: the three-way missing-data distinction (LAB-007) -----------------


def _health(obs, now=NOW, sources=("src_1",)):
    return assess_service_health(
        service_id="svc_1",
        observations=obs,
        source_ids=sources,
        now=now,
    )


def test_health_no_observations_is_unknown() -> None:
    report = _health([], sources=())
    assert report.state is HealthState.UNKNOWN
    assert report.condition is HealthCondition.NO_OBSERVATIONS
    assert "no_telemetry_sources" in report.unknowns


def test_health_silent_source_is_unknown_not_healthy() -> None:
    report = _health([], sources=("src_1",))
    assert report.state is HealthState.UNKNOWN
    assert "source_silent:src_1" in report.unknowns


def test_health_stale_source_is_unverified() -> None:
    obs = [_counter("old", 0, 10.0)]  # window ends 00:01; now is 00:10
    report = _health(obs)
    assert report.state is HealthState.UNVERIFIED
    assert report.condition is HealthCondition.SOURCE_STALE
    assert report.sources[0].stale is True


def test_health_no_traffic_is_unverified_not_down() -> None:
    # Fresh counter reporting zero requests — idle, never "down".
    obs = [_obs(obs_id="zero", start_min=9, value=0.0)]
    report = _health(obs)
    assert report.state is HealthState.UNVERIFIED
    assert report.condition is HealthCondition.NO_TRAFFIC


def test_health_available_when_clean() -> None:
    obs = [_counter("ok", 9, 100.0, attributes={"http.response.status_code": 200})]
    report = _health(obs)
    assert report.state is HealthState.AVAILABLE
    assert report.condition is HealthCondition.OBSERVED
    assert report.latest_indicators is not None
    assert report.latest_indicators.eligible_requests == 100.0


def test_health_partial_on_some_errors() -> None:
    obs = [
        _counter("ok", 9, 90.0, attributes={"http.response.status_code": 200}),
        _counter("err", 9, 10.0, attributes={"http.response.status_code": 500}),
    ]
    report = _health(obs)
    assert report.state is HealthState.PARTIAL
    assert report.condition is HealthCondition.DEGRADED


def test_health_unavailable_only_on_evidence() -> None:
    obs = [
        _obs(
            obs_id="err",
            start_min=9,
            value=100.0,
            attributes={"http.response.status_code": 503},
        )
    ]
    report = _health(obs)
    assert report.state is HealthState.UNAVAILABLE
    assert report.condition is HealthCondition.FAILING


def test_health_sampled_only_is_unmeasurable() -> None:
    obs = [_counter("s", 9, 500.0, quality=QualityState.SAMPLED)]
    report = _health(obs)
    assert report.state is HealthState.UNVERIFIED
    assert report.condition is HealthCondition.UNMEASURABLE


def test_health_absent_indicators_reported() -> None:
    obs = [_counter("ok", 9, 100.0)]
    report = _health(obs)
    # Counter source supplies no distribution — latency indicators absent.
    assert "latency_p95_ms" in report.absent_indicators
    assert report.latest_indicators is not None
    assert report.latest_indicators.latency_p95_ms is None
