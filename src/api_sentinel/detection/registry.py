"""Detector registry — closed set of versioned detectors (docs/08, ADR-007).

`DetectionRule` is the versioned rule entity: `rule_id` + `revision`
identify an immutable configuration (changing a rule means a new revision —
historical anomalies keep the rule revision that produced them).

The registry resolves a rule `kind` to its detector implementation. Kinds are
a closed, code-defined set — no arbitrary user-submitted rule execution —
and each detector validates its own config shape.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import Field

from api_sentinel.detection.protocol import Detector
from api_sentinel.detection.rules import (
    BudgetBurnAcceleration,
    ErrorRatioOverLimit,
    P95OverLatencyTarget,
    SourceSilence,
    StatusUnexpectedOnOperation,
    TimeoutGrowthAfterDeploy,
)
from api_sentinel.domain.errors import ValidationError
from api_sentinel.domain.identity import FrozenModel


class RulePolicy(FrozenModel):
    """Cross-cutting detection policy stored on the rule config.

    - `min_interval_seconds`: per-scope cooldown — a scope that produced an
      anomaly inside the interval is suppressed (flapping control, docs/08).
    - `confirm_windows`: hysteresis — the breach must hold in the current
      window and in `confirm_windows - 1` consecutive trailing windows of the
      same size; insufficient history yields an honest `unconfirmed`.
    - `suppressed_until`: planned suppression — while `now` is before the
      instant, the rule is skipped and the reason is recorded.
    """

    min_interval_seconds: int = Field(default=0, ge=0)
    confirm_windows: int = Field(default=1, ge=1)
    suppressed_until: datetime | None = None
    suppress_reason: str | None = None  # e.g. 'maintenance_window'


class DetectionRule(FrozenModel):
    """Versioned detection rule — immutable per (rule_id, revision)."""

    rule_id: str
    revision: int = Field(ge=1)
    service_id: str
    kind: str  # detector kind; closed set (registry)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    created_at: datetime

    def policy(self) -> RulePolicy:
        """Cross-cutting policy parsed out of `config` (kind params ignored)."""
        try:
            return RulePolicy.model_validate(self.config)
        except ValueError as exc:
            raise ValidationError(
                f"invalid rule policy on {self.rule_id}@{self.revision}: {exc}"
            ) from exc


# kind → detector factory(config dict). Closed set: unknown kinds are a
# named error, never executed or defaulted.
_DETECTOR_KINDS: dict[str, Callable[[dict[str, Any]], Detector]] = {
    ErrorRatioOverLimit.KIND: ErrorRatioOverLimit,
    P95OverLatencyTarget.KIND: P95OverLatencyTarget,
    SourceSilence.KIND: SourceSilence,
    BudgetBurnAcceleration.KIND: BudgetBurnAcceleration,
    StatusUnexpectedOnOperation.KIND: StatusUnexpectedOnOperation,
    TimeoutGrowthAfterDeploy.KIND: TimeoutGrowthAfterDeploy,
}


class DetectorRegistry:
    """Resolves versioned rules to detector instances; registers new kinds."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[dict[str, Any]], Detector]] = dict(
            _DETECTOR_KINDS
        )

    def register(
        self, kind: str, factory: Callable[[dict[str, Any]], Detector]
    ) -> None:
        self._factories[kind] = factory

    def kinds(self) -> list[str]:
        return sorted(self._factories)

    def create(self, rule: DetectionRule) -> Detector:
        factory = self._factories.get(rule.kind)
        if factory is None:
            raise ValidationError(
                f"unknown detection rule kind: {rule.kind!r}",
                details={
                    "rule_id": rule.rule_id,
                    "supported": sorted(self._factories),
                },
            )
        try:
            return factory(rule.config)
        except ValueError as exc:
            raise ValidationError(
                f"invalid config for rule kind {rule.kind!r}: {exc}",
                details={"rule_id": rule.rule_id, "revision": rule.revision},
            ) from exc


def default_rules(service_id: str, created_at: datetime) -> list[DetectionRule]:
    """Revision-1 built-in Level-1 rules for a service (deterministic ids —
    seeding is idempotent and replayable).

    Defaults are impact-policy placeholders (docs/09: real criteria are per
    service and organization): degradation rules default to SEV3, unexpected
    status to SEV4, source silence to INFORMATIONAL. `status_unexpected` is
    deliberately inert — nothing is 'unexpected' until the config declares it.
    """
    specs: list[tuple[str, dict[str, Any]]] = [
        (
            ErrorRatioOverLimit.KIND,
            {
                "max_error_ratio": 0.05,
                "min_requests": 10,
                "min_interval_seconds": 300,
                "confirm_windows": 1,
                "severity": "SEV3",
            },
        ),
        (
            P95OverLatencyTarget.KIND,
            {
                "max_p95_ms": 500.0,
                "min_requests": 10,
                "min_interval_seconds": 300,
                "confirm_windows": 1,
                "severity": "SEV3",
            },
        ),
        (
            SourceSilence.KIND,
            {
                "silent_after_seconds": 300,
                "min_interval_seconds": 300,
                "confirm_windows": 1,
                "severity": "INFORMATIONAL",
            },
        ),
        (
            BudgetBurnAcceleration.KIND,
            {
                "max_burn_rate": 2.0,
                "min_eligible": 1,
                "min_interval_seconds": 300,
                "confirm_windows": 1,
                "severity": "SEV3",
            },
        ),
        (
            StatusUnexpectedOnOperation.KIND,
            {
                "unexpected_status_codes": [],
                "unexpected_status_classes": [],
                "min_requests": 1,
                "min_interval_seconds": 300,
                "confirm_windows": 1,
                "severity": "SEV4",
            },
        ),
        (
            TimeoutGrowthAfterDeploy.KIND,
            {
                "max_timeout_ratio": 0.02,
                "min_requests": 10,
                "deploy_lookback_seconds": 3600,
                "min_interval_seconds": 300,
                "confirm_windows": 1,
                "severity": "SEV3",
            },
        ),
    ]
    # rule_id embeds service_id: rules are per-service entities, so default
    # ids must not collide across services under the (rule_id, revision) PK.
    return [
        DetectionRule(
            rule_id=f"rule_{kind}_{service_id}",
            revision=1,
            service_id=service_id,
            kind=kind,
            config=config,
            enabled=True,
            created_at=created_at,
        )
        for kind, config in specs
    ]
