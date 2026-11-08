"""Data-quality and signal enums — quality is orthogonal to conclusion."""

from __future__ import annotations

from enum import StrEnum


class QualityState(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    SAMPLED = "SAMPLED"
    STALE = "STALE"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class SignalType(StrEnum):
    COUNTER = "counter"
    HISTOGRAM = "histogram"
    GAUGE = "gauge"
    SPAN = "span"
    LOG = "log"
    DEPLOYMENT_EVENT = "deployment_event"
    CONFIGURATION_EVENT = "configuration_event"
    CONTRACT_EVENT = "contract_event"


class MeasurementKind(StrEnum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"
    EVENT = "event"


# Severity order shared by ingestion assessment and metrics aggregation:
# a worse state always wins and is never silently upgraded.
_QUALITY_SEVERITY = {
    QualityState.COMPLETE: 0,
    QualityState.UNKNOWN: 1,
    QualityState.PARTIAL: 2,
    QualityState.STALE: 3,
    QualityState.SAMPLED: 4,
    QualityState.INVALID: 5,
}


def worst_quality(a: QualityState, b: QualityState) -> QualityState:
    """The more severe of two quality states."""
    return a if _QUALITY_SEVERITY[a] >= _QUALITY_SEVERITY[b] else b


def worst_of(states: list[QualityState]) -> QualityState:
    """The worst state of a collection (UNKNOWN when empty)."""
    if not states:
        return QualityState.UNKNOWN
    worst = states[0]
    for s in states[1:]:
        worst = worst_quality(worst, s)
    return worst
