"""Rule evaluation policy — hysteresis, cooldown, suppression (docs/08).

Pure domain: wraps a detector's `evaluate` with the cross-cutting frequency
controls carried on `DetectionRule.config` (`RulePolicy`). No persistence —
the result says what should be persisted, suppressed or treated as replay.

Semantics:

- `confirm_windows` (hysteresis): the detector runs over the request window
  plus `confirm_windows - 1` consecutive trailing windows of equal size; a
  draft is confirmed only when its dedup scope fires in *every* window.
  Missing history is reported `unconfirmed`, never assumed clean.
- `min_interval_seconds` (cooldown): a confirmed draft whose scope already
  produced an anomaly inside the interval is suppressed — flapping control.
- Replay: a draft whose deterministic `anomaly_id` is already recorded is
  reported `already_recorded` — reprocessing never duplicates.
- Planned suppression: `suppressed_until` skips the rule honestly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import Field

from api_sentinel.detection.protocol import (
    Anomaly,
    AnomalyDraft,
    DetectionContext,
    DetectionRequest,
    DetectionResult,
    Detector,
    RuleRef,
    anomaly_id_for,
)
from api_sentinel.detection.registry import DetectionRule
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.metrics.windows import TimeWindow


class EvaluationOutcome(StrEnum):
    FIRED = "fired"  # at least one new anomaly confirmed
    CLEAN = "clean"  # evaluated, nothing fired
    SUPPRESSED = "suppressed"  # drafts existed; all suppressed/recorded
    SKIPPED_DISABLED = "skipped_disabled"
    SKIPPED_PLANNED_SUPPRESSION = "skipped_planned_suppression"


class SuppressedCandidate(FrozenModel):
    """A draft that did not become a new anomaly — with the honest reason."""

    dedup_key: str
    operation_id: str | None
    reason: str  # cooldown | unconfirmed:<have>/<need> | already_recorded
    detail: str | None = None


class RuleEvaluation(FrozenModel):
    """What one rule run decided — transparent enough to audit."""

    rule_id: str
    rule_revision: int
    kind: str
    detector_id: str
    detector_version: str
    outcome: EvaluationOutcome
    anomalies: list[AnomalyDraft] = Field(default_factory=list)
    recorded: list[str] = Field(default_factory=list)  # anomaly_ids (replay)
    suppressed: list[SuppressedCandidate] = Field(default_factory=list)
    confirm_windows: int = 1
    unknowns: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _draft_anomaly_id(rule: DetectionRule, draft: AnomalyDraft) -> str:
    return anomaly_id_for(
        rule_id=rule.rule_id,
        rule_revision=rule.revision,
        service_id=draft.service_id,
        operation_id=draft.operation_id,
        dedup_key=draft.dedup_key,
        window=draft.window,
    )


def _in_cooldown(
    rule: DetectionRule,
    draft: AnomalyDraft,
    recent: list[Anomaly],
    now: datetime,
    min_interval_seconds: int,
) -> bool:
    for a in recent:
        if a.rule.rule_id != rule.rule_id:
            continue
        if a.dedup_key != draft.dedup_key or a.operation_id != draft.operation_id:
            continue
        if 0 <= (now - a.detected_at).total_seconds() < min_interval_seconds:
            return True
    return False


def evaluate_rule(
    *,
    rule: DetectionRule,
    detector: Detector,
    request: DetectionRequest,
    context: DetectionContext,
) -> RuleEvaluation:
    """Evaluate one versioned rule over the request window + policy."""
    policy = rule.policy()

    def _result(
        outcome: EvaluationOutcome,
        *,
        anomalies: list[AnomalyDraft] | None = None,
        recorded: list[str] | None = None,
        suppressed: list[SuppressedCandidate] | None = None,
        unknowns: list[str] | None = None,
        limitations: list[str] | None = None,
        notes: list[str] | None = None,
    ) -> RuleEvaluation:
        return RuleEvaluation(
            rule_id=rule.rule_id,
            rule_revision=rule.revision,
            kind=rule.kind,
            detector_id=detector.detector_id,
            detector_version=detector.detector_version,
            confirm_windows=policy.confirm_windows,
            outcome=outcome,
            anomalies=anomalies or [],
            recorded=recorded or [],
            suppressed=suppressed or [],
            unknowns=unknowns or [],
            limitations=limitations or [],
            notes=notes or [],
        )

    if not rule.enabled:
        return _result(EvaluationOutcome.SKIPPED_DISABLED, notes=["rule_disabled"])
    if policy.suppressed_until is not None and request.now < policy.suppressed_until:
        return _result(
            EvaluationOutcome.SKIPPED_PLANNED_SUPPRESSION,
            notes=[f"planned_suppression:{policy.suppress_reason or 'suppressed_until'}"],
        )

    span = timedelta(seconds=request.window.seconds)
    windows = [
        TimeWindow(
            start=request.window.start - span * i,
            end=request.window.end - span * i,
        )
        for i in range(policy.confirm_windows)
    ]
    results: list[DetectionResult] = [
        detector.evaluate(request.model_copy(update={"window": w}), context) for w in windows
    ]
    current = results[0]
    unknowns = sorted({u for r in results for u in r.unknowns})
    limitations = sorted({x for r in results for x in r.limitations})
    notes = sorted({n for r in results for n in r.notes})
    if policy.confirm_windows > 1:
        notes.append(
            f"confirm_windows:{policy.confirm_windows} windows of "
            f"{request.window.seconds:g}s evaluated"
        )

    recent_ids = {a.anomaly_id for a in context.recent_anomalies}
    min_interval = policy.min_interval_seconds
    confirmed: list[AnomalyDraft] = []
    recorded: list[str] = []
    suppressed: list[SuppressedCandidate] = []

    for draft in current.anomalies:
        confirmations = 1 + sum(
            1 for r in results[1:] if any(d.dedup_key == draft.dedup_key for d in r.anomalies)
        )
        if confirmations < policy.confirm_windows:
            suppressed.append(
                SuppressedCandidate(
                    dedup_key=draft.dedup_key,
                    operation_id=draft.operation_id,
                    reason=f"unconfirmed:{confirmations}/{policy.confirm_windows}",
                    detail="breach did not hold in every required window",
                )
            )
            continue
        aid = _draft_anomaly_id(rule, draft)
        if aid in recent_ids:
            recorded.append(aid)
            continue
        if min_interval > 0 and _in_cooldown(
            rule, draft, context.recent_anomalies, request.now, min_interval
        ):
            suppressed.append(
                SuppressedCandidate(
                    dedup_key=draft.dedup_key,
                    operation_id=draft.operation_id,
                    reason="cooldown",
                    detail=f"scope alerted within {min_interval}s",
                )
            )
            continue
        confirmed.append(draft)

    outcome = EvaluationOutcome.CLEAN
    if confirmed:
        outcome = EvaluationOutcome.FIRED
    elif current.anomalies:
        outcome = EvaluationOutcome.SUPPRESSED
    return _result(
        outcome,
        anomalies=confirmed,
        recorded=sorted(recorded),
        suppressed=suppressed,
        unknowns=unknowns,
        limitations=limitations,
        notes=notes,
    )


def materialize_anomaly(*, rule: DetectionRule, detector: Detector, draft: AnomalyDraft) -> Anomaly:
    """Draft → persisted anomaly with deterministic identity."""
    return Anomaly(
        anomaly_id=_draft_anomaly_id(rule, draft),
        service_id=draft.service_id,
        operation_id=draft.operation_id,
        operation_ref=draft.operation_ref,
        detected_at=draft.detected_at,
        window_start=draft.window.start,
        window_end=draft.window.end,
        rule=RuleRef(
            rule_id=rule.rule_id,
            rule_revision=rule.revision,
            detector_id=detector.detector_id,
            detector_version=detector.detector_version,
        ),
        severity=draft.severity,
        explanation=draft.explanation,
        evidence_refs=draft.evidence_refs,
        unknowns=draft.unknowns,
        dedup_key=draft.dedup_key,
    )
