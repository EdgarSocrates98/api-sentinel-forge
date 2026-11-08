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
