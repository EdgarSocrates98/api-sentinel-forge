"""Unit oracles for deterministic detectors + evaluation policy (docs/08).

Expectations are hand-computed. Detectors are pure: the same request +
context returns an identical result, `detected_at` is always the injected
`now`, and detector instances hold no mutable state beyond parsed config.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api_sentinel.detection import (
    Anomaly,
    AnomalyStatus,
    DetectionContext,
    DetectionRequest,
    DetectionRule,
    Detector,
    DetectorRegistry,
    EvaluationOutcome,
    Explanation,
    RuleRef,
    Severity,
    anomaly_id_for,
    evaluate_rule,
    ewma,
    mad,
    materialize_anomaly,
    median,
    summarize_baselines,
    window_ratio,
)
from api_sentinel.detection.rules import (
    BudgetBurnAcceleration,
    ErrorRatioOverLimit,
    P95OverLatencyTarget,
    SourceSilence,
    StatusUnexpectedOnOperation,
    TimeoutGrowthAfterDeploy,
)
from api_sentinel.domain.errors import ValidationError
from api_sentinel.domain.models import ServiceRelease, TelemetrySource
from api_sentinel.domain.quality import MeasurementKind, QualityState
from api_sentinel.ingestion.models import (
    NormalizedMeasurement,
    NormalizedObservation,
    ObsBucket,
)
from api_sentinel.metrics import TimeWindow
from api_sentinel.reliability import AssessmentState, SliKind, SloAssessment

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 1, 1, 0, 12, tzinfo=UTC)
WIN = TimeWindow(start=T0, end=T0 + timedelta(minutes=10))


def _t(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _obs(
    *,
    obs_id: str,
    service: str = "svc_1",
    operation: str | None = "op_1",
    source: str = "src_1",
    start_min: float = 0.0,
    duration_min: float = 1.0,
    status: int | None = None,
    kind: MeasurementKind = MeasurementKind.COUNTER,
    unit: str | None = "requests",
    value: float | None = 10.0,
    count: int | None = None,
    buckets: list[tuple[float | str, int]] | None = None,
    attributes: dict | None = None,
) -> NormalizedObservation:
    start = _t(start_min)
    attrs = dict(attributes or {})
    if status is not None:
        attrs["http.response.status_code"] = status
        attrs.setdefault("http.request.method", "POST")
        attrs.setdefault("http.route", "/things/{id}")
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
        quality=QualityState.COMPLETE,
        measurement=NormalizedMeasurement(
            kind=kind,
            unit=unit,
            value=value,
            buckets=[ObsBucket(le=le, count=c) for le, c in (buckets or [])],
            count=count,
            temporality="delta",
        ),
        attributes=attrs,
        idempotency_key=f"k-{obs_id}",
    )


def _req(window: TimeWindow = WIN, now: datetime = NOW) -> DetectionRequest:
    return DetectionRequest(
        request_id="det_test", service_id="svc_1", window=window, now=now
    )


def _ctx(
    *,
    observations: list[NormalizedObservation] | None = None,
    sources: list[TelemetrySource] | None = None,
    assessments: list[SloAssessment] | None = None,
    releases: list[ServiceRelease] | None = None,
    recent: list[Anomaly] | None = None,
    now: datetime = NOW,
) -> DetectionContext:
    return DetectionContext(
        service_id="svc_1",
        now=now,
        observations=observations or [],
        slo_assessments=assessments or [],
        sources=sources or [],
        releases=releases or [],
        recent_anomalies=recent or [],
    )


def _rule(
    kind: str, config: dict, *, rule_id: str | None = None, enabled: bool = True
) -> DetectionRule:
    return DetectionRule(
        rule_id=rule_id or f"rule_{kind}",
        revision=1,
        service_id="svc_1",
        kind=kind,
        config=config,
        enabled=enabled,
        created_at=T0,
    )


def _source(source_id: str = "src_1", enabled: bool = True) -> TelemetrySource:
    return TelemetrySource(
        source_id=source_id,
        kind="jsonl-file",
        service_id="svc_1",
        enabled=enabled,
        created_at=T0,
    )


def _assessment(*, bad: float, eligible: float = 1000.0) -> SloAssessment:
    return SloAssessment(
        assessment_id="asmt_x",
        slo_id="slo_avail",
        revision=1,
        service_id="svc_1",
        operation_id=None,
        sli_kind=SliKind.AVAILABILITY,
        target=0.99,
        window_start=T0,
        window_end=T0 + timedelta(minutes=10),
        sli=(eligible - bad) / eligible,
        eligible=eligible,
        good=eligible - bad,
        bad=bad,
        budget_total=10.0,
        budget_consumed=None,
        budget_remaining=None,
        state=AssessmentState.MET,
        quality=QualityState.COMPLETE,
        rule_id="rule_slo",
        rule_version="1",
        primary_source_id="src_1",
        evaluated_at=NOW,
    )


def test_builtin_detectors_satisfy_protocol() -> None:
    for cls in (
        ErrorRatioOverLimit,
        P95OverLatencyTarget,
        SourceSilence,
        BudgetBurnAcceleration,
        StatusUnexpectedOnOperation,
        TimeoutGrowthAfterDeploy,
    ):
        assert isinstance(cls({}), Detector)


# --- detector purity --------------------------------------------------------


def test_detectors_are_deterministic_and_stateless() -> None:
    """Same request+context → identical result; detected_at is the injected
    now; a detector instance carries no mutable state beyond its config."""
    obs = [_obs(obs_id=f"o{i}", start_min=i, value=10, status=503) for i in range(10)]
    request = _req()
    for cls in (
        ErrorRatioOverLimit,
        P95OverLatencyTarget,
        StatusUnexpectedOnOperation,
    ):
        detector = cls({})
        assert set(detector.__dict__) == {"_cfg"}
        first = detector.evaluate(request, _ctx(observations=obs))
        second = detector.evaluate(request, _ctx(observations=obs))
        assert first == second
        assert first.model_dump() == second.model_dump()
        for draft in first.anomalies:
            assert draft.detected_at == NOW  # injected clock, never real time


def test_detected_at_tracks_only_the_injected_now() -> None:
    obs = [_obs(obs_id=f"o{i}", start_min=i, value=10, status=503) for i in range(10)]
    detector = ErrorRatioOverLimit({"max_error_ratio": 0.05})
    later = datetime(2030, 5, 5, 5, 5, tzinfo=UTC)
    result = detector.evaluate(_req(now=later), _ctx(observations=obs, now=later))
    assert all(a.detected_at == later for a in result.anomalies)


# --- error_ratio_over_limit -------------------------------------------------


def test_error_ratio_fires_over_limit() -> None:
    obs = [_obs(obs_id="ok", start_min=0, duration_min=10, value=90, status=200)]
    obs.append(
        _obs(obs_id="err", start_min=0, duration_min=10, value=10, status=503)
    )
    detector = ErrorRatioOverLimit({"max_error_ratio": 0.05})
    result = detector.evaluate(_req(), _ctx(observations=obs))
    (draft,) = result.anomalies
    # 10 errors / 100 eligible = 0.10 > 0.05
    assert "0.1000" in draft.explanation.math
    assert draft.severity is Severity.SEV3
    assert draft.dedup_key == "op:op_1"
    assert "obs:err" in draft.evidence_refs


def test_error_ratio_silent_on_clean_input() -> None:
    obs = [
        _obs(obs_id="ok", start_min=0, duration_min=10, value=98, status=200),
        _obs(obs_id="err", start_min=0, duration_min=10, value=2, status=503),
    ]
    detector = ErrorRatioOverLimit({"max_error_ratio": 0.05})
    result = detector.evaluate(_req(), _ctx(observations=obs))
    assert result.anomalies == []


def test_error_ratio_low_traffic_is_a_note_not_an_anomaly() -> None:
    obs = [_obs(obs_id="err", start_min=0, duration_min=10, value=5, status=503)]
    detector = ErrorRatioOverLimit({"max_error_ratio": 0.05, "min_requests": 10})
    result = detector.evaluate(_req(), _ctx(observations=obs))
    assert result.anomalies == []
    assert any("low_traffic" in n for n in result.notes)


def test_error_ratio_no_observations_is_an_unknown() -> None:
    result = ErrorRatioOverLimit({}).evaluate(_req(), _ctx())
    assert result.anomalies == []
    assert result.unknowns == ["no_observations_in_window"]


# --- p95_over_latency_target ------------------------------------------------


def _hist(obs_id: str, start_min: float, buckets, *, count: int = 100) -> NormalizedObservation:
    return _obs(
        obs_id=obs_id,
        start_min=start_min,
        kind=MeasurementKind.HISTOGRAM,
        unit="ms",
        value=None,
        count=count,
        buckets=buckets,
    )


def test_p95_fires_over_latency_target() -> None:
    # Merged cumulative 5,10,20,50,100,100 → rank 95 lands in le=1000:
    # 500 + 500*(95-50)/50 = 950ms > 500ms default target.
    obs = [
        _hist(
            "h1",
            0,
            [(50, 5), (100, 10), (200, 20), (500, 50), (1000, 100), ("+Inf", 100)],
            count=100,
        )
    ]
    detector = P95OverLatencyTarget({"max_p95_ms": 500.0, "min_requests": 10})
    result = detector.evaluate(_req(), _ctx(observations=obs))
    (draft,) = result.anomalies
    assert "950" in draft.explanation.math
    assert "never averaged" in draft.explanation.math


def test_p95_silent_below_target() -> None:
    # 10,60,90,99,100,100 → rank 95 lands in le=500:
    # 200 + 300*(95-90)/9 ≈ 366.7ms < 500ms.
    obs = [
        _hist(
            "h1",
            0,
            [(50, 10), (100, 60), (200, 90), (500, 99), (1000, 100), ("+Inf", 100)],
            count=100,
        )
    ]
    result = P95OverLatencyTarget({"max_p95_ms": 500.0}).evaluate(
        _req(), _ctx(observations=obs)
    )
    assert result.anomalies == []


# --- source_silence ---------------------------------------------------------


def test_source_silence_fires_after_horizon() -> None:
    obs = [_obs(obs_id="o1", start_min=0, duration_min=5, value=10)]
    detector = SourceSilence({"silent_after_seconds": 300})
    result = detector.evaluate(
        _req(), _ctx(observations=obs, sources=[_source()])
    )
    (draft,) = result.anomalies
    # last window_end = 00:05; NOW=00:12 → 420s silent > 300s
    assert "420s" in draft.explanation.math
    assert draft.severity is Severity.INFORMATIONAL
    assert "not proof of outage" in draft.explanation.summary
    assert "service_state_unmeasurable_while_silent" in draft.unknowns


def test_source_silence_clean_when_fresh() -> None:
    obs = [_obs(obs_id="o1", start_min=8, duration_min=2, value=10)]
    detector = SourceSilence({"silent_after_seconds": 300})
    # latest end = 00:10; NOW=00:12 → 120s < 300s
    result = detector.evaluate(_req(), _ctx(observations=obs, sources=[_source()]))
    assert result.anomalies == []


def test_source_never_observed_is_an_unknown() -> None:
    result = SourceSilence({}).evaluate(_req(), _ctx(sources=[_source("src_new")]))
    assert result.anomalies == []
    assert "source_never_observed:src_new" in result.unknowns


def test_source_silence_no_sources_is_an_unknown() -> None:
    result = SourceSilence({}).evaluate(_req(), _ctx())
    assert result.unknowns == ["no_telemetry_sources"]


def test_disabled_source_is_a_note() -> None:
    result = SourceSilence({}).evaluate(
        _req(), _ctx(sources=[_source("src_off", enabled=False)])
    )
    assert result.anomalies == []
    assert "source_disabled:src_off" in result.notes


# --- budget_burn_acceleration -----------------------------------------------


def test_budget_burn_fires_on_acceleration() -> None:
    # bad/eligible = 50/1000 = 0.05; allowed = 1-0.99 = 0.01 → burn 5.0 ≥ 2.0
    detector = BudgetBurnAcceleration({"max_burn_rate": 2.0})
    result = detector.evaluate(
        _req(), _ctx(assessments=[_assessment(bad=50.0)])
    )
    (draft,) = result.anomalies
    assert "5.0000" in draft.explanation.math
    assert "asmt:asmt_x" in draft.evidence_refs
    assert draft.dedup_key == "slo:slo_avail"


def test_budget_burn_clean_at_sustainable_rate() -> None:
    detector = BudgetBurnAcceleration({"max_burn_rate": 2.0})
    result = detector.evaluate(
        _req(), _ctx(assessments=[_assessment(bad=5.0)])
    )
    assert result.anomalies == []


def test_budget_burn_no_assessments_is_an_unknown() -> None:
    result = BudgetBurnAcceleration({}).evaluate(_req(), _ctx())
    assert result.unknowns == ["no_slo_assessments_in_window"]


# --- status_unexpected_on_operation ------------------------------------------


def test_status_unexpected_inert_without_config() -> None:
    obs = [_obs(obs_id="o1", value=50, status=503)]
    result = StatusUnexpectedOnOperation({}).evaluate(
        _req(), _ctx(observations=obs)
    )
    assert result.anomalies == []
    assert "no_unexpected_statuses_configured" in result.unknowns


def test_status_unexpected_fires_on_declared_codes() -> None:
    obs = [
        _obs(obs_id="o1", value=25, status=404),
        _obs(obs_id="o2", value=75, status=200),
    ]
    detector = StatusUnexpectedOnOperation({"unexpected_status_classes": ["4xx"]})
    result = detector.evaluate(_req(), _ctx(observations=obs))
    (draft,) = result.anomalies
    assert "404:25" in draft.explanation.math
    assert draft.severity is Severity.SEV4


# --- timeout_growth_after_deploy ---------------------------------------------


def _release(
    release_id: str = "rel_1", deployed_at: datetime | None = None
) -> ServiceRelease:
    return ServiceRelease(
        release_id=release_id,
        service_id="svc_1",
        version="v2.4.0",
        deployed_at=deployed_at if deployed_at is not None else T0,
        created_at=T0,
    )


def _timeout_obs(obs_id: str, start_min: float, value: float) -> NormalizedObservation:
    return _obs(
        obs_id=obs_id,
        start_min=start_min,
        value=value,
        attributes={"error.kind": "timeout"},
    )


def test_timeout_growth_fires_after_deploy() -> None:
    # 70 timeouts / 100 eligible = 0.70 > 0.02; release inside the lookback.
    obs = [
        *[_timeout_obs(f"t{i}", i, 10) for i in range(7)],
        _obs(obs_id="ok", value=30, status=200),
    ]
    detector = TimeoutGrowthAfterDeploy(
        {"max_timeout_ratio": 0.02, "deploy_lookback_seconds": 3600}
    )
    result = detector.evaluate(
        _req(), _ctx(observations=obs, releases=[_release()])
    )
    (draft,) = result.anomalies
    assert "0.7000" in draft.explanation.math
    assert any(r.startswith("rel:rel_1@") for r in draft.evidence_refs)
    # Evidence, never cause — the disclaimer is part of the result.
    assert "temporal_proximity_is_not_causation" in result.limitations
    assert draft.dedup_key == "op:op_1:timeout"


def test_timeout_growth_without_release_is_a_note() -> None:
    obs = [_timeout_obs(f"t{i}", i, 10) for i in range(7)]
    detector = TimeoutGrowthAfterDeploy({"max_timeout_ratio": 0.02})
    result = detector.evaluate(_req(), _ctx(observations=obs))
    assert result.anomalies == []
    assert "no_release_in_lookback:op_1" in result.notes


def test_timeout_growth_release_outside_lookback_is_a_note() -> None:
    obs = [_timeout_obs(f"t{i}", i, 10) for i in range(7)]
    old_release = _release(deployed_at=T0 - timedelta(hours=5))
    detector = TimeoutGrowthAfterDeploy(
        {"max_timeout_ratio": 0.02, "deploy_lookback_seconds": 3600}
    )
    result = detector.evaluate(
        _req(), _ctx(observations=obs, releases=[old_release])
    )
    assert result.anomalies == []
    assert "no_release_in_lookback:op_1" in result.notes


def test_timeout_growth_clean_without_timeouts() -> None:
    obs = [_obs(obs_id="ok", value=100, status=200)]
    result = TimeoutGrowthAfterDeploy({}).evaluate(
        _req(), _ctx(observations=obs, releases=[_release()])
    )
    assert result.anomalies == []


# --- baselines: evaluated on a known corpus, never a cause --------------------


def test_median_mad_ewma_window_ratio_oracles() -> None:
    assert median([1.0, 5.0, 9.0]) == 5.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert median([]) is None
    assert mad([1.0, 5.0, 9.0]) == 4.0  # median(|1-5|,|5-5|,|9-5|)=median(4,0,4)
    assert ewma([10.0, 20.0], alpha=0.5) == 15.0
    assert ewma([], alpha=0.5) is None
    assert window_ratio(10.0, 5.0) == 2.0
    assert window_ratio(10.0, 0.0) is None  # undefined reported absent


def test_summarize_baselines_on_known_corpus() -> None:
    # Two windows strictly before the detection window: 10% errors then 0%.
    corpus = [
        _obs(obs_id="ok0", start_min=0, value=90, status=200),
        _obs(obs_id="err0", start_min=0, value=10, status=503),
        _obs(obs_id="ok1", start_min=1, value=100, status=200),
    ]
    baselines = {
        (b.metric, b.method): b
        for b in summarize_baselines(
            corpus, service_id="svc_1", before_window_start=_t(5)
        )
    }
    err = baselines[("error_ratio", "median+mad")]
    # median of [0.1, 0.0] = 0.05; MAD = median(|0.1-0.05|,|0.0-0.05|) = 0.05.
    assert err.value == pytest.approx(0.05)
    assert err.deviation == pytest.approx(0.05)
    assert err.windows_used == 2
    # A baseline describes expected behavior — it never claims root cause.
    assert baselines[("error_ratio", "ewma")].value is not None


# --- registry + named errors -------------------------------------------------


def test_registry_unknown_kind_is_a_named_error() -> None:
    registry = DetectorRegistry()
    with pytest.raises(ValidationError, match="unknown detection rule kind"):
        registry.create(_rule("bogus_kind", {}))


def test_registry_invalid_config_is_a_named_error() -> None:
    registry = DetectorRegistry()
    with pytest.raises(ValidationError, match="invalid config"):
        registry.create(
            _rule("error_ratio_over_limit", {"max_error_ratio": 2.0})
        )


def test_registry_kinds_is_the_closed_set() -> None:
    assert DetectorRegistry().kinds() == sorted(
        [
            "budget_burn_acceleration",
            "error_ratio_over_limit",
            "p95_over_latency_target",
            "source_silence",
            "status_unexpected_on_operation",
            "timeout_growth_after_deploy",
        ]
    )


# --- evaluate_rule policy: hysteresis, cooldown, suppression ------------------


def _breach_obs(start_min: float) -> list[NormalizedObservation]:
    return [
        _obs(obs_id=f"ok{start_min}", start_min=start_min, value=90, status=200),
        _obs(obs_id=f"err{start_min}", start_min=start_min, value=10, status=503),
    ]


def test_hysteresis_requires_breach_in_every_confirm_window() -> None:
    rule = _rule(
        "error_ratio_over_limit",
        {"max_error_ratio": 0.05, "min_requests": 1, "confirm_windows": 2},
    )
    detector = DetectorRegistry().create(rule)
    window = TimeWindow(start=_t(10), end=_t(20))
    # Breach only in the current window → unconfirmed, honestly reported.
    out = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(window),
        context=_ctx(observations=_breach_obs(12)),
    )
    assert out.outcome is EvaluationOutcome.SUPPRESSED
    assert out.suppressed[0].reason == "unconfirmed:1/2"

    # Same breach holding in the trailing window too → confirmed.
    out = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(window),
        context=_ctx(observations=[*_breach_obs(12), *_breach_obs(2)]),
    )
    assert out.outcome is EvaluationOutcome.FIRED
    assert len(out.anomalies) == 1


def test_cooldown_suppresses_flapping() -> None:
    rule = _rule(
        "error_ratio_over_limit",
        {"max_error_ratio": 0.05, "min_requests": 1, "min_interval_seconds": 300},
    )
    detector = DetectorRegistry().create(rule)
    prior = Anomaly(
        anomaly_id="anm_prior",
        service_id="svc_1",
        operation_id="op_1",
        detected_at=NOW - timedelta(seconds=120),
        window_start=T0,
        window_end=_t(5),
        rule=RuleRef(
            rule_id=rule.rule_id,
            rule_revision=1,
            detector_id=detector.detector_id,
            detector_version=detector.detector_version,
        ),
        severity=Severity.SEV3,
        explanation=Explanation(summary="earlier breach"),
        dedup_key="op:op_1",
    )
    out = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(),
        context=_ctx(observations=_breach_obs(0), recent=[prior]),
    )
    assert out.outcome is EvaluationOutcome.SUPPRESSED
    assert out.suppressed[0].reason == "cooldown"

    # Outside the cooldown interval the same scope fires again.
    older = prior.model_copy(update={"detected_at": NOW - timedelta(seconds=600)})
    out = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(),
        context=_ctx(observations=_breach_obs(0), recent=[older]),
    )
    assert out.outcome is EvaluationOutcome.FIRED


def test_already_recorded_replay_never_duplicates() -> None:
    rule = _rule(
        "error_ratio_over_limit",
        {"max_error_ratio": 0.05, "min_requests": 1},
    )
    detector = DetectorRegistry().create(rule)
    obs = _breach_obs(0)
    first = evaluate_rule(
        rule=rule, detector=detector, request=_req(), context=_ctx(observations=obs)
    )
    assert first.outcome is EvaluationOutcome.FIRED
    anomaly = materialize_anomaly(rule=rule, detector=detector, draft=first.anomalies[0])
    assert anomaly.anomaly_id == anomaly_id_for(
        rule_id=rule.rule_id,
        rule_revision=1,
        service_id="svc_1",
        operation_id="op_1",
        dedup_key="op:op_1",
        window=WIN,
    )
    replayed = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(),
        context=_ctx(observations=obs, recent=[anomaly]),
    )
    assert replayed.outcome is EvaluationOutcome.SUPPRESSED
    assert replayed.anomalies == []
    assert replayed.recorded == [anomaly.anomaly_id]


def test_disabled_rule_is_skipped_honestly() -> None:
    rule = _rule(
        "error_ratio_over_limit", {"max_error_ratio": 0.05}, enabled=False
    )
    detector = DetectorRegistry().create(rule)
    out = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(),
        context=_ctx(observations=_breach_obs(0)),
    )
    assert out.outcome is EvaluationOutcome.SKIPPED_DISABLED


def test_planned_suppression_is_recorded_not_silent() -> None:
    rule = _rule(
        "error_ratio_over_limit",
        {
            "max_error_ratio": 0.05,
            "min_requests": 1,
            "suppressed_until": NOW + timedelta(hours=1),
            "suppress_reason": "maintenance_window",
        },
    )
    detector = DetectorRegistry().create(rule)
    out = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(),
        context=_ctx(observations=_breach_obs(0)),
    )
    assert out.outcome is EvaluationOutcome.SKIPPED_PLANNED_SUPPRESSION
    assert out.notes == ["planned_suppression:maintenance_window"]


def test_result_carries_rule_identity_and_explanation() -> None:
    """Every anomaly keeps rule id+revision+detector version + math + evidence."""
    rule = _rule("error_ratio_over_limit", {"max_error_ratio": 0.05, "min_requests": 1})
    detector = DetectorRegistry().create(rule)
    out = evaluate_rule(
        rule=rule,
        detector=detector,
        request=_req(),
        context=_ctx(observations=_breach_obs(0)),
    )
    anomaly = materialize_anomaly(rule=rule, detector=detector, draft=out.anomalies[0])
    assert anomaly.rule == RuleRef(
        rule_id="rule_error_ratio_over_limit",
        rule_revision=1,
        detector_id="error_ratio_over_limit",
        detector_version="1",
    )
    assert anomaly.explanation.math
    assert anomaly.evidence_refs
    assert anomaly.severity is Severity.SEV3
    assert anomaly.status is AnomalyStatus.OBSERVED
