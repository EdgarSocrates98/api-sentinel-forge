"""Level-1 deterministic detectors (docs/08 §Detection engine, ADR-007).

Every detector is a pure evaluator behind the `Detector` protocol: no
network, no real clock (only `request.now`/`context.now`), no credentials,
no implicit persistence. Each anomaly explains the exact math that fired it
and records rule identity; severity comes from the versioned rule config —
impact-driven policy, never HTTP status codes alone (docs/09).

Built-in rules:

- `error_ratio_over_limit` — (errors+timeouts)/eligible over the window.
- `p95_over_latency_target` — p95 over the latency objective, per operation.
- `source_silence` — a registered source stopped sending (missing telemetry
  is its own honest condition — never reported as service outage).
- `budget_burn_acceleration` — error-budget consumption accelerating vs the
  sustainable rate `1 - S` over the assessed population.
- `status_unexpected_on_operation` — statuses declared unexpected by the
  rule's explicit config appear on an operation.
- `timeout_growth_after_deploy` — the timeout share of requests crosses a
  configured limit while a release deployed inside the lookback horizon.
  The release is recorded as *evidence*, never as cause — temporal
  proximity is not causation (docs/08, Cycle-5 scope for correlation).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import Field, field_validator

from api_sentinel.detection.protocol import (
    AnomalyDraft,
    DetectionContext,
    DetectionRequest,
    DetectionResult,
    Explanation,
    Severity,
    operation_ref,
)
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import MeasurementKind
from api_sentinel.ingestion.models import NormalizedObservation
from api_sentinel.metrics.aggregates import (
    ErrorClassification,
    WindowAggregate,
    compute_window_aggregates,
)
from api_sentinel.metrics.windows import TimeWindow

DETECTOR_VERSION = "1"
_EVIDENCE_LIMIT = 20
_STATUS_ATTR = "http.response.status_code"


def _fmt(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _aggregates(
    context: DetectionContext, window: TimeWindow, *, by_operation: bool
) -> list[WindowAggregate]:
    scoped = [o for o in context.observations if o.service_id == context.service_id]
    return compute_window_aggregates(
        scoped, window, ErrorClassification(), by_operation=by_operation
    )


def _group_observations(
    context: DetectionContext, aggregate: WindowAggregate
) -> list[NormalizedObservation]:
    window = TimeWindow(start=aggregate.window_start, end=aggregate.window_end)
    return [
        o
        for o in context.observations
        if o.service_id == aggregate.service_id
        and o.operation_id == aggregate.operation_id
        and o.source_id == aggregate.source_id
        and window.contains(o)
    ]


def _obs_evidence(obs: list[NormalizedObservation]) -> list[str]:
    ids = sorted(o.observation_id for o in obs)
    refs = [f"obs:{i}" for i in ids[:_EVIDENCE_LIMIT]]
    if len(ids) > _EVIDENCE_LIMIT:
        refs.append(f"obs:+{len(ids) - _EVIDENCE_LIMIT}_more")
    return refs


def _scope_label(aggregate: WindowAggregate, obs: list[NormalizedObservation]) -> str:
    ref = next(
        (r for r in (operation_ref(o.attributes, o.operation_id) for o in obs) if r),
        aggregate.operation_id,
    )
    return ref or "service"


class ErrorRatioOverLimit:
    """error_ratio = (errors + timeouts) / eligible over the request window.

    Guards against low-traffic over-alerting via `min_requests` (docs/08:
    one bad request in a quiet service can burn a large budget fraction).
    """

    KIND = "error_ratio_over_limit"
    VERSION = DETECTOR_VERSION

    class Config(FrozenModel):
        max_error_ratio: float = Field(default=0.05, gt=0.0, le=1.0)
        min_requests: float = Field(default=10.0, ge=0.0)
        severity: Severity = Severity.SEV3

    def __init__(self, config: dict[str, Any]) -> None:
        self._cfg = self.Config.model_validate(config)

    @property
    def detector_id(self) -> str:
        return self.KIND

    @property
    def detector_version(self) -> str:
        return self.VERSION

    def evaluate(self, request: DetectionRequest, context: DetectionContext) -> DetectionResult:
        cfg = self._cfg
        anomalies: list[AnomalyDraft] = []
        notes: list[str] = []
        aggregates = _aggregates(context, request.window, by_operation=True)
        if not aggregates:
            return DetectionResult(
                detector_id=self.KIND,
                detector_version=self.VERSION,
                unknowns=["no_observations_in_window"],
            )
        for agg in aggregates:
            if agg.plane is None:
                notes.append(f"no_request_plane:{agg.operation_id or 'service'}")
                continue
            if agg.eligible_requests < cfg.min_requests:
                notes.append(
                    f"low_traffic:{agg.operation_id or 'service'}:"
                    f"{agg.eligible_requests:g}<{cfg.min_requests:g}"
                )
                continue
            ratio = agg.error_ratio
            if ratio is None or ratio <= cfg.max_error_ratio:
                continue
            obs = _group_observations(context, agg)
            scope = _scope_label(agg, obs)
            anomalies.append(
                AnomalyDraft(
                    service_id=agg.service_id,
                    operation_id=agg.operation_id,
                    operation_ref=scope if scope != "service" else None,
                    detected_at=request.now,
                    window=request.window,
                    severity=cfg.severity,
                    explanation=Explanation(
                        summary=(
                            f"error ratio {ratio:.2%} over limit "
                            f"{cfg.max_error_ratio:.2%} on {scope}"
                        ),
                        math=(
                            f"error_ratio = (errors+timeouts)/eligible = "
                            f"({agg.error_requests:g}+{agg.timeout_requests:g})/"
                            f"{agg.eligible_requests:g} = {_fmt(ratio)} > "
                            f"{cfg.max_error_ratio:g}"
                        ),
                    ),
                    evidence_refs=[*_obs_evidence(obs), f"src:{agg.source_id}"],
                    unknowns=[f"agg_limitations:{lim}" for lim in agg.limitations],
                    dedup_key=f"op:{agg.operation_id or 'service'}",
                )
            )
        return DetectionResult(
            detector_id=self.KIND,
            detector_version=self.VERSION,
            anomalies=anomalies,
            notes=sorted(set(notes)),
        )


class P95OverLatencyTarget:
    """p95 over the latency objective — evaluated per operation scope."""

    KIND = "p95_over_latency_target"
    VERSION = DETECTOR_VERSION

    class Config(FrozenModel):
        max_p95_ms: float = Field(default=500.0, gt=0.0)
        min_requests: float = Field(default=10.0, ge=0.0)
        severity: Severity = Severity.SEV3

    def __init__(self, config: dict[str, Any]) -> None:
        self._cfg = self.Config.model_validate(config)

    @property
    def detector_id(self) -> str:
        return self.KIND

    @property
    def detector_version(self) -> str:
        return self.VERSION

    def evaluate(self, request: DetectionRequest, context: DetectionContext) -> DetectionResult:
        cfg = self._cfg
        anomalies: list[AnomalyDraft] = []
        notes: list[str] = []
        unknowns: list[str] = []
        aggregates = _aggregates(context, request.window, by_operation=True)
        if not aggregates:
            return DetectionResult(
                detector_id=self.KIND,
                detector_version=self.VERSION,
                unknowns=["no_observations_in_window"],
            )
        for agg in aggregates:
            if agg.plane is None:
                continue
            if agg.latency_p95_ms is None:
                notes.append(f"no_latency_distribution:{agg.operation_id or 'service'}")
                continue
            if agg.eligible_requests < cfg.min_requests:
                notes.append(
                    f"low_traffic:{agg.operation_id or 'service'}:"
                    f"{agg.eligible_requests:g}<{cfg.min_requests:g}"
                )
                continue
            if agg.latency_p95_ms <= cfg.max_p95_ms:
                continue
            obs = _group_observations(context, agg)
            scope = _scope_label(agg, obs)
            p95 = agg.latency_p95_ms
            anomalies.append(
                AnomalyDraft(
                    service_id=agg.service_id,
                    operation_id=agg.operation_id,
                    operation_ref=scope if scope != "service" else None,
                    detected_at=request.now,
                    window=request.window,
                    severity=cfg.severity,
                    explanation=Explanation(
                        summary=(
                            f"p95 latency {p95:g}ms over target {cfg.max_p95_ms:g}ms on {scope}"
                        ),
                        math=(
                            f"p95 = {p95:g}ms > {cfg.max_p95_ms:g}ms over "
                            f"eligible={agg.eligible_requests:g} merged "
                            "buckets (interpolated, never averaged)"
                        ),
                    ),
                    evidence_refs=[*_obs_evidence(obs), f"src:{agg.source_id}"],
                    unknowns=[f"agg_limitations:{lim}" for lim in agg.limitations],
                    dedup_key=f"op:{agg.operation_id or 'service'}",
                )
            )
        return DetectionResult(
            detector_id=self.KIND,
            detector_version=self.VERSION,
            anomalies=anomalies,
            unknowns=sorted(set(unknowns)),
            notes=sorted(set(notes)),
        )


class SourceSilence:
    """A registered, enabled source stopped sending observations.

    Honest semantics (docs/08): missing telemetry is its own detection case —
    it says collection stopped, NOT that the service is down. A source that
    never sent anything is an unknown, not an anomaly.
    """

    KIND = "source_silence"
    VERSION = DETECTOR_VERSION

    class Config(FrozenModel):
        silent_after_seconds: int = Field(default=300, gt=0)
        severity: Severity = Severity.INFORMATIONAL

    def __init__(self, config: dict[str, Any]) -> None:
        self._cfg = self.Config.model_validate(config)

    @property
    def detector_id(self) -> str:
        return self.KIND

    @property
    def detector_version(self) -> str:
        return self.VERSION

    def evaluate(self, request: DetectionRequest, context: DetectionContext) -> DetectionResult:
        cfg = self._cfg
        anomalies: list[AnomalyDraft] = []
        unknowns: list[str] = []
        notes: list[str] = []
        registered = [s for s in context.sources if s.service_id == context.service_id]
        if not registered:
            return DetectionResult(
                detector_id=self.KIND,
                detector_version=self.VERSION,
                unknowns=["no_telemetry_sources"],
            )
        horizon_seconds = cfg.silent_after_seconds
        for source in sorted(registered, key=lambda s: s.source_id):
            if not source.enabled:
                notes.append(f"source_disabled:{source.source_id}")
                continue
            ends = [o.window_end for o in context.observations if o.source_id == source.source_id]
            if not ends:
                # Never observed: we cannot distinguish "stopped" from
                # "never started" — an explicit unknown, not an anomaly.
                unknowns.append(f"source_never_observed:{source.source_id}")
                continue
            latest_end = max(ends)
            silent_for = (request.now - latest_end).total_seconds()
            if silent_for <= horizon_seconds:
                continue
            anomalies.append(
                AnomalyDraft(
                    service_id=context.service_id,
                    detected_at=request.now,
                    window=request.window,
                    severity=cfg.severity,
                    explanation=Explanation(
                        summary=(
                            f"telemetry source {source.source_id} silent for "
                            f"{int(silent_for)}s — collection stopped, service "
                            "state unknown (not proof of outage)"
                        ),
                        math=(
                            f"last_window_end={latest_end.isoformat()}; "
                            f"now-last = {int(silent_for)}s > "
                            f"{horizon_seconds}s"
                        ),
                    ),
                    evidence_refs=[
                        f"src:{source.source_id}",
                        f"last_window_end:{latest_end.isoformat()}",
                    ],
                    unknowns=["service_state_unmeasurable_while_silent"],
                    dedup_key=f"src:{source.source_id}",
                )
            )
        return DetectionResult(
            detector_id=self.KIND,
            detector_version=self.VERSION,
            anomalies=anomalies,
            unknowns=sorted(set(unknowns)),
            notes=sorted(set(notes)),
        )


class BudgetBurnAcceleration:
    """Error-budget consumption accelerating vs the sustainable rate.

    burn_rate = observed_error_ratio / allowed_error_ratio = (bad/eligible) /
    (1 - S). Rate 1.0 consumes the window budget exactly on pace; > 1.0 is
    acceleration. Reads `SloAssessment` slices only — the SLO math stays in
    the reliability context.
    """

    KIND = "budget_burn_acceleration"
    VERSION = DETECTOR_VERSION

    class Config(FrozenModel):
        max_burn_rate: float = Field(default=2.0, gt=0.0)
        min_eligible: float = Field(default=1.0, ge=0.0)
        severity: Severity = Severity.SEV3

    def __init__(self, config: dict[str, Any]) -> None:
        self._cfg = self.Config.model_validate(config)

    @property
    def detector_id(self) -> str:
        return self.KIND

    @property
    def detector_version(self) -> str:
        return self.VERSION

    def evaluate(self, request: DetectionRequest, context: DetectionContext) -> DetectionResult:
        cfg = self._cfg
        anomalies: list[AnomalyDraft] = []
        notes: list[str] = []
        assessments = [
            a
            for a in context.slo_assessments
            if a.service_id == context.service_id
            and a.window_start < request.window.end
            and a.window_end > request.window.start
        ]
        if not assessments:
            return DetectionResult(
                detector_id=self.KIND,
                detector_version=self.VERSION,
                unknowns=["no_slo_assessments_in_window"],
            )
        # Latest assessment per (slo_id, revision) inside the window.
        latest: dict[tuple[str, int], Any] = {}
        for a in assessments:
            key = (a.slo_id, a.revision)
            if key not in latest or a.window_end > latest[key].window_end:
                latest[key] = a
        for (slo_id, _revision), a in sorted(latest.items()):
            if a.eligible < cfg.min_eligible or a.eligible <= 0:
                notes.append(f"low_traffic:{slo_id}:{a.eligible:g}")
                continue
            allowed = 1.0 - a.target
            if allowed <= 0:
                notes.append(f"zero_allowed_ratio:{slo_id}")
                continue
            observed = a.bad / a.eligible
            burn_rate = observed / allowed
            if burn_rate < cfg.max_burn_rate:
                continue
            anomalies.append(
                AnomalyDraft(
                    service_id=a.service_id,
                    operation_id=a.operation_id,
                    operation_ref=a.operation_id,
                    detected_at=request.now,
                    window=request.window,
                    severity=cfg.severity,
                    explanation=Explanation(
                        summary=(
                            f"error budget burn accelerating on {slo_id}: "
                            f"{burn_rate:.2f}x the sustainable rate"
                        ),
                        math=(
                            f"burn_rate = (bad/eligible)/(1-S) = "
                            f"({a.bad:g}/{a.eligible:g})/{allowed:.4f} = "
                            f"{burn_rate:.4f} >= {cfg.max_burn_rate:g}"
                        ),
                    ),
                    evidence_refs=[
                        f"asmt:{a.assessment_id}",
                        f"slo:{slo_id}@{a.revision}",
                    ],
                    unknowns=[f"assessment_limitations:{x}" for x in a.limitations],
                    dedup_key=f"slo:{slo_id}",
                )
            )
        return DetectionResult(
            detector_id=self.KIND,
            detector_version=self.VERSION,
            anomalies=anomalies,
            notes=sorted(set(notes)),
        )


class StatusUnexpectedOnOperation:
    """Statuses declared unexpected by the rule config appear on an operation.

    Inert by default: an empty `unexpected_*` configuration means *nothing is
    unexpected* — the detector reports the missing policy as an unknown and
    never guesses which statuses should alarm.
    """

    KIND = "status_unexpected_on_operation"
    VERSION = DETECTOR_VERSION

    class Config(FrozenModel):
        unexpected_status_codes: list[int] = Field(default_factory=list)
        unexpected_status_classes: list[str] = Field(default_factory=list)
        min_requests: float = Field(default=1.0, ge=0.0)
        severity: Severity = Severity.SEV4

        @field_validator("unexpected_status_classes")
        @classmethod
        def _classes(cls, v: list[str]) -> list[str]:
            for klass in v:
                if len(klass) != 3 or not klass[0].isdigit() or klass[1:] != "xx":
                    raise ValueError(f"invalid status class: {klass!r} (expected e.g. '5xx')")
            return v

    def __init__(self, config: dict[str, Any]) -> None:
        self._cfg = self.Config.model_validate(config)

    @property
    def detector_id(self) -> str:
        return self.KIND

    @property
    def detector_version(self) -> str:
        return self.VERSION

    def _unexpected(self, status: int) -> bool:
        cfg = self._cfg
        if status in cfg.unexpected_status_codes:
            return True
        return any(
            int(klass[0]) * 100 <= status <= int(klass[0]) * 100 + 99
            for klass in cfg.unexpected_status_classes
        )

    @staticmethod
    def _weight(obs: NormalizedObservation) -> float:
        m = obs.measurement
        if m.kind == MeasurementKind.COUNTER:
            return float(m.value or 0.0)
        if m.kind in (MeasurementKind.HISTOGRAM, MeasurementKind.SUMMARY):
            return float(m.count or 0.0)
        return 0.0

    def evaluate(self, request: DetectionRequest, context: DetectionContext) -> DetectionResult:
        cfg = self._cfg
        if not cfg.unexpected_status_codes and not cfg.unexpected_status_classes:
            return DetectionResult(
                detector_id=self.KIND,
                detector_version=self.VERSION,
                unknowns=["no_unexpected_statuses_configured"],
            )
        anomalies: list[AnomalyDraft] = []
        limitations: list[str] = []
        hits: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        for obs in context.observations:
            if obs.service_id != context.service_id or not request.window.contains(obs):
                continue
            status = obs.attributes.get(_STATUS_ATTR)
            try:
                code = int(status)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if not self._unexpected(code):
                continue
            if obs.measurement.temporality == "cumulative":
                limitations.append("cumulative_status_observation_skipped")
                continue
            weight = self._weight(obs)
            key = (obs.operation_id, operation_ref(obs.attributes, obs.operation_id))
            slot = hits.setdefault(key, {"count": 0.0, "statuses": {}, "obs": []})
            slot["count"] += weight
            slot["statuses"][code] = slot["statuses"].get(code, 0.0) + weight
            slot["obs"].append(obs)
        for (operation_id, ref), slot in sorted(hits.items(), key=lambda kv: str(kv[0])):
            count = float(slot["count"])
            if count < cfg.min_requests:
                continue
            statuses = slot["statuses"]
            status_text = ",".join(f"{code}:{n:g}" for code, n in sorted(statuses.items()))
            scope = ref or "service"
            anomalies.append(
                AnomalyDraft(
                    service_id=context.service_id,
                    operation_id=operation_id,
                    operation_ref=scope if scope != "service" else None,
                    detected_at=request.now,
                    window=request.window,
                    severity=cfg.severity,
                    explanation=Explanation(
                        summary=(f"unexpected status on {scope}: {status_text} requests in window"),
                        math=(
                            f"unexpected status requests = {count:g} >= "
                            f"{cfg.min_requests:g}; statuses {status_text} "
                            "declared unexpected by rule config"
                        ),
                    ),
                    evidence_refs=_obs_evidence(slot["obs"]),
                    dedup_key=f"op:{operation_id or 'service'}:status",
                )
            )
        return DetectionResult(
            detector_id=self.KIND,
            detector_version=self.VERSION,
            anomalies=anomalies,
            limitations=sorted(set(limitations)),
        )


class TimeoutGrowthAfterDeploy:
    """Timeout share over limit while a release deployed inside the lookback.

    Level-1 semantics (docs/08): `timeout_ratio = timeouts/eligible` over the
    request window, per operation scope. The anomaly fires only when the
    share crosses `max_timeout_ratio` *and* a release for the service has a
    `deployed_at` within `deploy_lookback_seconds` of the window end — the
    release is attached as evidence and an explicit
    `temporal_proximity_is_not_causation` limitation rides every anomaly.
    A silent/no-release window is reported honestly as a note.
    """

    KIND = "timeout_growth_after_deploy"
    VERSION = DETECTOR_VERSION

    class Config(FrozenModel):
        max_timeout_ratio: float = Field(default=0.02, gt=0.0, le=1.0)
        min_requests: float = Field(default=10.0, ge=0.0)
        deploy_lookback_seconds: int = Field(default=3600, gt=0)
        severity: Severity = Severity.SEV3

    def __init__(self, config: dict[str, Any]) -> None:
        self._cfg = self.Config.model_validate(config)

    @property
    def detector_id(self) -> str:
        return self.KIND

    @property
    def detector_version(self) -> str:
        return self.VERSION

    def evaluate(self, request: DetectionRequest, context: DetectionContext) -> DetectionResult:
        cfg = self._cfg
        anomalies: list[AnomalyDraft] = []
        notes: list[str] = []
        limitations: list[str] = []
        aggregates = _aggregates(context, request.window, by_operation=True)
        if not aggregates:
            return DetectionResult(
                detector_id=self.KIND,
                detector_version=self.VERSION,
                unknowns=["no_observations_in_window"],
            )
        horizon_start = request.window.end - timedelta(seconds=cfg.deploy_lookback_seconds)
        releases = sorted(
            (
                r
                for r in context.releases
                if r.service_id == context.service_id
                and r.deployed_at is not None
                and horizon_start <= r.deployed_at <= request.window.end
            ),
            key=lambda r: (r.deployed_at or request.window.end, r.release_id),
        )
        release_refs = [f"rel:{r.release_id}@{r.version}" for r in releases]
        release_text = "; releases in lookback: " + ", ".join(release_refs) if releases else ""
        for agg in aggregates:
            if agg.plane is None or agg.eligible_requests < cfg.min_requests:
                continue
            if agg.timeout_requests <= 0:
                continue
            ratio = agg.timeout_requests / agg.eligible_requests
            if ratio <= cfg.max_timeout_ratio:
                continue
            if not releases:
                notes.append(f"no_release_in_lookback:{agg.operation_id or 'service'}")
                continue
            obs = _group_observations(context, agg)
            scope = _scope_label(agg, obs)
            limitations.append("temporal_proximity_is_not_causation")
            anomalies.append(
                AnomalyDraft(
                    service_id=agg.service_id,
                    operation_id=agg.operation_id,
                    operation_ref=scope if scope != "service" else None,
                    detected_at=request.now,
                    window=request.window,
                    severity=cfg.severity,
                    explanation=Explanation(
                        summary=(
                            f"timeout share {ratio:.2%} over limit "
                            f"{cfg.max_timeout_ratio:.2%} on {scope} after "
                            "a deploy inside the lookback horizon"
                        ),
                        math=(
                            f"timeout_ratio = timeouts/eligible = "
                            f"{agg.timeout_requests:g}/{agg.eligible_requests:g} = "
                            f"{_fmt(ratio)} > {cfg.max_timeout_ratio:g}" + release_text
                        ),
                    ),
                    evidence_refs=[
                        *_obs_evidence(obs),
                        f"src:{agg.source_id}",
                        *release_refs,
                    ],
                    unknowns=[f"agg_limitations:{lim}" for lim in agg.limitations],
                    dedup_key=f"op:{agg.operation_id or 'service'}:timeout",
                )
            )
        return DetectionResult(
            detector_id=self.KIND,
            detector_version=self.VERSION,
            anomalies=anomalies,
            limitations=sorted(set(limitations)),
            notes=sorted(set(notes)),
        )
