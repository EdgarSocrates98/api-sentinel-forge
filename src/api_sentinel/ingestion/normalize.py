"""Normalize stage — units, semantic-convention variants, attribute hygiene.

Rules (docs/05): explicit units only — an unknown unit flags the item INVALID,
it is never silently assumed. Durations normalize to ms; conversion factors are
lossless within float64 and recorded in transform_notes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from api_sentinel.domain.quality import MeasurementKind
from api_sentinel.ingestion.models import (
    Measurement,
    NormalizedMeasurement,
    ObsBucket,
)

# unit → (canonical unit, multiplicative factor). Anything absent is INVALID.
_UNIT_TABLE: dict[str, tuple[str, float]] = {
    "ms": ("ms", 1.0),
    "millisecond": ("ms", 1.0),
    "milliseconds": ("ms", 1.0),
    "s": ("ms", 1000.0),
    "sec": ("ms", 1000.0),
    "second": ("ms", 1000.0),
    "seconds": ("ms", 1000.0),
    "us": ("ms", 0.001),
    "µs": ("ms", 0.001),
    "microsecond": ("ms", 0.001),
    "microseconds": ("ms", 0.001),
    "ns": ("ms", 1e-6),
    "nanosecond": ("ms", 1e-6),
    "nanoseconds": ("ms", 1e-6),
    "min": ("ms", 60_000.0),
    "minute": ("ms", 60_000.0),
    "minutes": ("ms", 60_000.0),
    "requests": ("requests", 1.0),
    "request": ("requests", 1.0),
    "count": ("count", 1.0),
    "1": ("count", 1.0),  # OTLP dimensionless
    "ratio": ("ratio", 1.0),
    "percent": ("ratio", 0.01),
    "%": ("ratio", 0.01),
    "bytes": ("bytes", 1.0),
    "by": ("bytes", 1.0),
}

# OTel HTTP semantic-convention renames we accept (legacy → stable).
_ATTRIBUTE_VARIANTS: dict[str, tuple[str, str]] = {
    "http.method": ("http.request.method", "legacy"),
    "http.status_code": ("http.response.status_code", "legacy"),
    "http.scheme": ("url.scheme", "legacy"),
    "http.host": ("server.address", "legacy"),
    "http.target": ("url.path", "legacy"),
    "http.request.method": ("http.request.method", "stable"),
    "http.response.status_code": ("http.response.status_code", "stable"),
    "http.route": ("http.route", "stable"),
    "url.scheme": ("url.scheme", "stable"),
    "url.path": ("url.path", "stable"),
    "server.address": ("server.address", "stable"),
}

_NUMERIC_KINDS = {
    MeasurementKind.COUNTER,
    MeasurementKind.GAUGE,
    MeasurementKind.HISTOGRAM,
    MeasurementKind.SUMMARY,
}


def canonical_unit(unit: str) -> tuple[str, float] | None:
    """(canonical, factor) for an explicit unit, else None → caller flags INVALID."""
    return _UNIT_TABLE.get(unit.strip().lower())


def attribute_variant(key: str) -> tuple[str, str]:
    """Map an OTel attribute key to (canonical key, semconv variant tag)."""
    return _ATTRIBUTE_VARIANTS.get(key, (key, "custom"))


def _convert_bucket(raw: dict[str, Any], factor: float) -> ObsBucket | None:
    le, count = raw.get("le"), raw.get("count")
    if count is None:
        return None
    if isinstance(le, str):
        if le.strip().lower() in {"+inf", "inf", "infinity"}:
            return ObsBucket(le="+Inf", count=int(count))
        return None
    if isinstance(le, (int, float)):
        return ObsBucket(le=float(le) * factor, count=int(count))
    return None


def normalize_measurement(m: Measurement) -> tuple[NormalizedMeasurement, list[str]]:
    """Convert units losslessly; return issues that downgrade quality.

    The kind is always preserved — a histogram is never reduced to a counter,
    and nothing here ever averages percentiles or distributions.
    """
    issues: list[str] = []
    notes = list(m.transform_notes)
    factor = 1.0
    unit = m.unit
    if m.kind in _NUMERIC_KINDS:
        if unit is None:
            issues.append("missing_unit")
            unit = None
        else:
            resolved = canonical_unit(unit)
            if resolved is None:
                issues.append("unknown_unit")
            else:
                canonical, factor = resolved
                if canonical != unit:
                    notes.append(f"unit:{unit}→{canonical}")
                unit = canonical
    buckets: list[ObsBucket] = []
    for raw in m.buckets:
        b = _convert_bucket(raw, factor)
        if b is None:
            issues.append("bad_bucket")
        else:
            buckets.append(b)
    normalized = NormalizedMeasurement(
        kind=m.kind,
        unit=unit,
        value=None if m.value is None else m.value * factor,
        buckets=buckets,
        sum=None if m.sum is None else m.sum * factor,
        count=m.count,
        temporality=m.temporality,
        transform_notes=notes,
    )
    if m.kind == MeasurementKind.HISTOGRAM and m.buckets and not buckets:
        issues.append("bad_bucket")
    return normalized, issues


def redact_attributes(
    attrs: dict[str, Any], redactor: Callable[[str], str]
) -> tuple[dict[str, Any], int]:
    """Apply the secret redactor to every string attribute value; returns
    (cleaned, number of values that changed). Never drops the key."""
    cleaned: dict[str, Any] = {}
    changed = 0
    for key, value in attrs.items():
        if isinstance(value, str):
            out = redactor(value)
            if out != value:
                changed += 1
            cleaned[key] = out
        else:
            cleaned[key] = value
    return cleaned, changed
