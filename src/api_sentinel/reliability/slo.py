"""SLO definitions and windowed assessments — the reliability context core.

- `SloDefinition` is versioned: `slo_id` + `revision` identify an immutable
  objective `S` over an SLI and a window. Changing an SLO creates a new
  revision — historical assessments are never rewritten (LAB-015).
- `SloAssessment` is one reproducible evaluation of a definition over a
  window: same inputs + same revision ⇒ identical result (deterministic
  `assessment_id`, injected `evaluated_at`, recorded rule/slo revisions).
- Formulas are docs/08's: `SLI = good/eligible`, `B = N(1-S)`,
  `consumed = bad / N(1-S)` — valid only when counters share population
  definition, window and coverage. Violations surface as limitations.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.quality import QualityState
from api_sentinel.metrics.aggregates import (
    ErrorClassification,
    WindowAggregate,
    compute_window_aggregates,
    cumulative_at,
)
from api_sentinel.metrics.windows import ObservationLike, TimeWindow
from api_sentinel.reliability.budget import budget_consumed, budget_remaining, error_budget
from api_sentinel.reliability.sli import SliKind, sli_value

# Evaluator identity recorded on every assessment — bump RULE_VERSION only with
# a deliberate evaluation-semantics change (never silently).
RULE_ID = "slo.evaluate"
RULE_VERSION = "1"


class SloThresholds(FrozenModel):
    """Thresholds an SLI may need; latency SLI requires `latency_ms`."""

    latency_ms: float | None = Field(default=None, gt=0)


class SloDefinition(FrozenModel):
    """Versioned reliability objective (`slo_id` + `revision`)."""

    slo_id: str
    revision: int = Field(ge=1)
    service_id: str
    operation_id: str | None = None
    sli_kind: SliKind = SliKind.AVAILABILITY
    target: float = Field(gt=0.0, lt=1.0)  # S, e.g. 0.99
    window_seconds: int = Field(gt=0)
    thresholds: SloThresholds = Field(default_factory=SloThresholds)
    error_classification: ErrorClassification = Field(default_factory=ErrorClassification)
    created_at: datetime

    @model_validator(mode="after")
    def _latency_needs_threshold(self) -> SloDefinition:
        if self.sli_kind is SliKind.LATENCY and self.thresholds.latency_ms is None:
            raise ValueError("latency SLO requires thresholds.latency_ms")
        return self


class AssessmentState(StrEnum):
    """Operational conclusion of one window evaluation.

    Quality stays a separate dimension (ADR-017/018): `quality` records the
    data-quality state while `state` records only the SLO math outcome.
    """

    MET = "MET"
    VIOLATED = "VIOLATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INCOMPATIBLE_INPUTS = "INCOMPATIBLE_INPUTS"


class SourceAssessment(FrozenModel):
    """One source's view of the window — sources are identified, never summed."""

    source_id: str
    eligible: float
    good: float
    bad: float
    sli: float | None
    quality: QualityState
    source_kind: str | None = None
    """TelemetrySource.kind when known (simulated|fixture|real|…) — recorded
    so a conclusion is never silently read as production telemetry."""


class SloCoverage(FrozenModel):
    """Completeness/representativeness of what fed the assessment."""

    sources_used: list[str] = Field(default_factory=list)
    observations_included: int = 0
    observations_excluded: int = 0
    excluded_by_reason: dict[str, int] = Field(default_factory=dict)
    window_seconds: float = 0.0


class SloAssessment(FrozenModel):
    """One reproducible window evaluation of a SloDefinition revision."""

    assessment_id: str
    slo_id: str
    revision: int
    service_id: str
    operation_id: str | None
    sli_kind: SliKind
    target: float
    window_start: datetime
    window_end: datetime
    sli: float | None
    eligible: float
    good: float
    bad: float
    budget_total: float | None
    budget_consumed: float | None
    budget_remaining: float | None
    state: AssessmentState
    quality: QualityState
    rule_id: str
    rule_version: str
    primary_source_id: str | None
    per_source: list[SourceAssessment] = Field(default_factory=list)
    coverage: SloCoverage = Field(default_factory=SloCoverage)
    limitations: list[str] = Field(default_factory=list)
    evaluated_at: datetime


def assessment_id_for(slo_id: str, revision: int, window: TimeWindow) -> str:
    """Deterministic id of the (slo, revision, window) evaluation slot.

    Re-evaluating the same slot reproduces the same id — persistence replaces
    the row instead of duplicating it.
    """
    canonical = f"{slo_id}|{revision}|{window.start.isoformat()}|{window.end.isoformat()}"
    return "asmt_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _aggregate_sli(
    definition: SloDefinition, aggregate: WindowAggregate
) -> tuple[float | None, float, float]:
    """(sli, good, bad) for one source population under the definition.

    Availability: `good = eligible - (errors + timeouts)` — dedicated
    401/403/429 classes neither degrade the SLI nor consume budget unless the
    definition's error classification promotes them to errors.
    Latency: `good = within threshold` (interpolated over merged buckets).
    """
    eligible = aggregate.eligible_requests
    if definition.sli_kind is SliKind.LATENCY:
        threshold = definition.thresholds.latency_ms
        if threshold is None or not aggregate.latency_buckets:
            return None, 0.0, 0.0
        good = cumulative_at(aggregate.latency_buckets, threshold)
        return sli_value(good, eligible), good, eligible - good
    bad = aggregate.error_requests + aggregate.timeout_requests
    good = eligible - bad
    return sli_value(good, eligible), good, bad


_SIMULATED_KINDS = frozenset({"simulated", "fixture", "synthetic", "demo"})


def evaluate_slo(
    *,
    definition: SloDefinition,
    observations: Iterable[ObservationLike],
    window: TimeWindow,
    evaluated_at: datetime,
    source_kinds: dict[str, str] | None = None,
) -> SloAssessment:
    """Reproducible evaluation: pure function of inputs + injected time.

    Population rule: SLI math runs per source (sources never sum). When more
    than one source covers the window, the primary is chosen deterministically
    — largest eligible population, then `source_id` — and every source's view
    is reported under `per_source` with a `multiple_sources_not_summed`
    limitation rather than mixed into one number.

    `source_kinds` carries TelemetrySource provenance (kind per source_id).
    A non-production kind (`simulated`/`fixture`/`synthetic`/`demo`) never
    changes the math — it *labels* it: `simulated_source_data` marks any
    contributing synthetic source and `simulated_primary_source` marks the
    primary, so a MET on simulated traffic can never masquerade as real
    production health (C101-F009).
    """
    kinds = source_kinds or {}
    scoped = [o for o in observations if o.service_id == definition.service_id]
    aggregates = compute_window_aggregates(
        scoped,
        window,
        definition.error_classification,
        by_operation=definition.operation_id is not None,
    )
    if definition.operation_id is not None:
        aggregates = [a for a in aggregates if a.operation_id == definition.operation_id]

    limitations: list[str] = []
    excluded_by_reason: dict[str, int] = {}
    included = excluded = 0
    for agg in aggregates:
        included += agg.observations_included
        excluded += agg.observations_excluded
        for reason, count in agg.excluded_by_reason.items():
            excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + count
        limitations.extend(agg.limitations)
    coverage = SloCoverage(
        sources_used=sorted({a.source_id for a in aggregates}),
        observations_included=included,
        observations_excluded=excluded,
        excluded_by_reason=excluded_by_reason,
        window_seconds=window.seconds,
    )

    per_source = []
    simulated_sources: list[str] = []
    for a in sorted(aggregates, key=lambda x: x.source_id):
        src_sli, src_good, src_bad = _aggregate_sli(definition, a)
        kind = kinds.get(a.source_id)
        if kind is not None and kind.lower() in _SIMULATED_KINDS:
            simulated_sources.append(a.source_id)
        per_source.append(
            SourceAssessment(
                source_id=a.source_id,
                eligible=a.eligible_requests,
                good=src_good,
                bad=src_bad,
                sli=src_sli,
                quality=a.quality,
                source_kind=kind,
            )
        )
    if simulated_sources:
        limitations.append("simulated_source_data:" + ",".join(simulated_sources))

    primary: WindowAggregate | None = None
    if aggregates:
        if len(aggregates) > 1:
            limitations.append(
                "multiple_sources_not_summed:" + ",".join(sorted(a.source_id for a in aggregates))
            )
        # Deterministic primary: largest eligible population, then source_id.
        primary = min(aggregates, key=lambda a: (-a.eligible_requests, a.source_id))
        if primary.source_id in simulated_sources:
            limitations.append("simulated_primary_source")

    state: AssessmentState
    sli: float | None = None
    eligible = good = bad = 0.0
    quality = QualityState.UNKNOWN
    budget_total = budget_used = budget_left = None

    if primary is None:
        state = AssessmentState.INSUFFICIENT_DATA
        limitations.append("no_observations_in_window")
    elif primary.plane is None and primary.observations_included > 0:
        # Usable observations exist but no consistent request population can
        # be established from them — a mismatch, not an absence.
        state = AssessmentState.INCOMPATIBLE_INPUTS
        quality = primary.quality
        limitations.append("no_shared_request_population")
    elif primary.eligible_requests <= 0:
        state = AssessmentState.INSUFFICIENT_DATA
        quality = primary.quality
        limitations.append(
            "all_observations_excluded" if included == 0 else "no_requests_in_window"
        )
    else:
        sli, good, bad = _aggregate_sli(definition, primary)
        eligible = primary.eligible_requests
        quality = primary.quality
        if sli is None:
            state = AssessmentState.INSUFFICIENT_DATA
            if definition.sli_kind is SliKind.LATENCY:
                limitations.append("no_latency_distribution")
        else:
            state = AssessmentState.MET if sli >= definition.target else AssessmentState.VIOLATED
            budget_total = error_budget(eligible, definition.target)
            budget_used = budget_consumed(bad, eligible, definition.target)
            budget_left = budget_remaining(bad, eligible, definition.target)
            if budget_used is None:
                limitations.append("zero_budget_denominator")

    return SloAssessment(
        assessment_id=assessment_id_for(definition.slo_id, definition.revision, window),
        slo_id=definition.slo_id,
        revision=definition.revision,
        service_id=definition.service_id,
        operation_id=definition.operation_id,
        sli_kind=definition.sli_kind,
        target=definition.target,
        window_start=window.start,
        window_end=window.end,
        sli=sli,
        eligible=eligible,
        good=good,
        bad=bad,
        budget_total=budget_total,
        budget_consumed=budget_used,
        budget_remaining=budget_left,
        state=state,
        quality=quality,
        rule_id=RULE_ID,
        rule_version=RULE_VERSION,
        primary_source_id=primary.source_id if primary else None,
        per_source=per_source,
        coverage=coverage,
        limitations=sorted(set(limitations)),
        evaluated_at=evaluated_at,
    )
