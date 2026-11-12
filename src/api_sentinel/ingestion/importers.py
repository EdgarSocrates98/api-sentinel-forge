"""Importers — Acquire + Validate stages.

Two MVP sources (docs/05): normalized JSONL lines of RuntimeObservationV1, and
a documented OTLP JSON subset. Everything an importer cannot validate becomes a
QuarantineCandidate — a batch never crashes on a bad item, and nothing unknown
is silently defaulted.

Supported OTLP subset (explicitly bounded):
- top-level `resourceMetrics[]` → `scopeMetrics[]` → `metrics[]`
- metrics: `http.server.request.duration` (stable semconv) and
  `http.server.duration` (legacy semconv), histogram data points only
- attribute variants: `http.request.method`/`http.method`,
  `http.response.status_code`/`http.status_code`, `http.route`, `url.scheme`,
  `server.address`/`http.host`
- explicit-bounds histograms (bucketCounts → cumulative `le` buckets),
  cumulative|delta temporality carried — never collapsed
- int64 fields arrive as JSON strings per OTLP/JSON mapping; both forms accepted
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from api_sentinel.contracts.v1.models import RuntimeObservationV1
from api_sentinel.domain.errors import ContractError
from api_sentinel.domain.quality import MeasurementKind, QualityState
from api_sentinel.ingestion.dedup import deterministic_observation_id
from api_sentinel.ingestion.models import (
    ImportedItem,
    ImportOutcome,
    Measurement,
    QuarantineCandidate,
)
from api_sentinel.ingestion.normalize import attribute_variant

_KINDS = ("jsonl", "otlp-json")

# name → semconv variant tag; anything else is quarantined, never guessed
_SUPPORTED_METRICS = {
    "http.server.request.duration": "stable",
    "http.server.duration": "legacy",
}

_TEMPORALITY = {1: "delta", 2: "cumulative"}

# candidate payloads are bounded at import; the persisted bound (and the
# truncation flag) is applied later by quarantine.make_entry
_PREVIEW_LIMIT = 64 * 1024


def detect_kind(content: bytes) -> str:
    """Sniff the payload shape; default is the JSONL normalized format."""
    head = content.lstrip()[:2048]
    if head.startswith(b"{") and b'"resourceMetrics"' in head:
        return "otlp-json"
    return "jsonl"


def _preview(text: str | bytes) -> str:
    if isinstance(text, bytes):
        return text[:_PREVIEW_LIMIT].decode("utf-8", errors="replace")
    return text[:_PREVIEW_LIMIT]


def parse_jsonl(content: bytes) -> ImportOutcome:
    """One RuntimeObservationV1 per line; per-line failures → quarantine."""
    items: list[ImportedItem] = []
    quarantine: list[QuarantineCandidate] = []
    total = 0
    for raw in content.split(b"\n"):
        line = raw.strip()
        if not line:
            continue
        total += 1
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            quarantine.append(QuarantineCandidate("undecodable_line", _preview(line)))
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            quarantine.append(QuarantineCandidate("invalid_json", _preview(text)))
            continue
        if not isinstance(obj, dict):
            quarantine.append(QuarantineCandidate("not_an_object", _preview(text)))
            continue
        if obj.get("schema") != RuntimeObservationV1.expected_schema:
            # unknown schema/version → explicit rejection, never defaulted
            quarantine.append(QuarantineCandidate("unknown_schema", _preview(text)))
            continue
        try:
            env = RuntimeObservationV1.model_validate(obj)
            measurement = Measurement.model_validate(env.measurement.model_dump(exclude_none=True))
        except (PydanticValidationError, ContractError, ValueError):
            quarantine.append(QuarantineCandidate("schema_validation", _preview(text)))
            continue
        attributes = dict(env.attributes)
        if env.sampling is not None:
            if env.sampling.method:
                attributes["sampling.method"] = env.sampling.method
            if env.sampling.rate is not None:
                attributes["sampling.rate"] = env.sampling.rate
        items.append(
            ImportedItem(
                observation_id=env.observation_id,
                service_id=env.service_id,
                operation_id=env.operation_id,
                release_id=env.release_id,
                window_start=env.window.start,
                window_end=env.window.end,
                declared_source_id=env.source_id,
                declared_quality=env.quality,
                sampling_rate=env.sampling.rate if env.sampling else None,
                dropped=False,
                measurement=measurement,
                attributes=attributes,
            )
        )
    return ImportOutcome(
        items=items,
        quarantine=quarantine,
        total_items=total,
        checkpoint=str(total),
        importer="jsonl",
    )


def _any_value(value: dict[str, Any]) -> Any:
    """OTLP AnyValue → plain Python (stringValue/intValue/doubleValue/...)."""
    if not isinstance(value, dict):
        return value
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "arrayValue" in value:
        return [_any_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return {
            e.get("key"): _any_value(e.get("value", {}))
            for e in value["kvlistValue"].get("values", [])
        }
    if "bytesValue" in value:
        return value["bytesValue"]
    return None


def _kv_list(raw: Any) -> dict[str, Any]:
    """OTLP attribute list [{key, value:{...}}] → plain dict."""
    out: dict[str, Any] = {}
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if isinstance(entry, dict) and "key" in entry:
            out[str(entry["key"])] = _any_value(entry.get("value", {}))
    return out


def _ns_to_dt(value: Any) -> datetime:
    ns = int(value)
    return datetime.fromtimestamp(ns / 1e9, tz=UTC)


def _int(value: Any) -> int:
    return int(value)


class _Malformed(Exception):
    pass


def _histogram_buckets(dp: dict[str, Any]) -> list[dict[str, Any]]:
    """OTel explicit-bounds → cumulative `le` buckets (lossless semantics)."""
    bounds = dp.get("explicitBounds") or []
    counts = dp.get("bucketCounts") or []
    try:
        bounds_f = [float(b) for b in bounds]
        counts_i = [_int(c) for c in counts]
    except (TypeError, ValueError) as exc:
        raise _Malformed("bad histogram buckets") from exc
    if len(counts_i) != len(bounds_f) + 1:
        raise _Malformed("bucketCounts must be len(explicitBounds)+1")
    buckets: list[dict[str, Any]] = []
    cumulative = 0
    for i, bound in enumerate(bounds_f):
        cumulative += counts_i[i]
        buckets.append({"le": bound, "count": cumulative})
    buckets.append({"le": "+Inf", "count": sum(counts_i)})
    return buckets


def parse_otlp_json(
    content: bytes, *, resolve_service: Callable[[str], str | None]
) -> ImportOutcome:
    """Documented OTLP JSON subset → ImportedItems; everything else quarantined."""
    try:
        doc = json.loads(content)
    except json.JSONDecodeError:
        return ImportOutcome(
            items=[],
            quarantine=[QuarantineCandidate("invalid_json", _preview(content))],
            total_items=1,
            checkpoint=str(len(content)),
            importer="otlp-json",
        )
    if not isinstance(doc, dict) or not isinstance(doc.get("resourceMetrics"), list):
        return ImportOutcome(
            items=[],
            quarantine=[QuarantineCandidate("unsupported_shape", _preview(content))],
            total_items=1,
            checkpoint=str(len(content)),
            importer="otlp-json",
        )

    items: list[ImportedItem] = []
    quarantine: list[QuarantineCandidate] = []
    for rm in doc["resourceMetrics"]:
        if not isinstance(rm, dict):
            quarantine.append(
                QuarantineCandidate("malformed_resource_metrics", _preview(json.dumps(rm)))
            )
            continue
        resource_attrs = _kv_list((rm.get("resource") or {}).get("attributes"))
        service_name = resource_attrs.get("service.name")
        resolved_service_id = (
            resolve_service(str(service_name)) if service_name is not None else None
        )
        unresolved_service = service_name is not None and resolved_service_id is None
        scope_metrics = rm.get("scopeMetrics") or []
        for sm in scope_metrics:
            if not isinstance(sm, dict):
                quarantine.append(
                    QuarantineCandidate("malformed_scope_metrics", _preview(json.dumps(sm)))
                )
                continue
            for metric in sm.get("metrics") or []:
                if not isinstance(metric, dict):
                    quarantine.append(
                        QuarantineCandidate("unsupported_metric", _preview(json.dumps(metric)))
                    )
                    continue
                name = metric.get("name")
                if name not in _SUPPORTED_METRICS:
                    quarantine.append(
                        QuarantineCandidate("unsupported_metric", _preview(json.dumps(metric)))
                    )
                    continue
                variant = _SUPPORTED_METRICS[name]
                if not isinstance(metric.get("histogram"), dict):
                    quarantine.append(
                        QuarantineCandidate("unsupported_datatype", _preview(json.dumps(metric)))
                    )
                    continue
                histogram = metric["histogram"]
                temporality = _TEMPORALITY.get(histogram.get("aggregationTemporality"))
                points = histogram.get("dataPoints") or []
                for dp in points:
                    if unresolved_service:
                        quarantine.append(
                            QuarantineCandidate("unresolved_service", _preview(json.dumps(dp)))
                        )
                        continue
                    try:
                        item = _convert_point(
                            dp,
                            metric=metric,
                            name=name,
                            variant=variant,
                            temporality=temporality,
                            resource_attrs=resource_attrs,
                            resolved_service_id=resolved_service_id,
                        )
                    except (_Malformed, KeyError, TypeError, ValueError):
                        quarantine.append(
                            QuarantineCandidate("malformed_datapoint", _preview(json.dumps(dp)))
                        )
                        continue
                    items.append(item)
    return ImportOutcome(
        items=items,
        quarantine=quarantine,
        total_items=len(items) + len(quarantine),
        checkpoint=str(len(content)),
        importer="otlp-json",
    )


def _convert_point(
    dp: dict[str, Any],
    *,
    metric: dict[str, Any],
    name: str,
    variant: str,
    temporality: str | None,
    resource_attrs: dict[str, Any],
    resolved_service_id: str | None,
) -> ImportedItem:
    if not isinstance(dp, dict) or "timeUnixNano" not in dp:
        raise _Malformed("missing timeUnixNano")
    window_end = _ns_to_dt(dp["timeUnixNano"])
    window_start = _ns_to_dt(dp["startTimeUnixNano"]) if dp.get("startTimeUnixNano") else window_end
    point_attrs = _kv_list(dp.get("attributes"))

    attributes: dict[str, Any] = {}
    variants = {variant}
    for key, value in point_attrs.items():
        canonical_key, key_variant = attribute_variant(key)
        attributes[canonical_key] = value
        if key_variant != "custom":
            variants.add(key_variant)
    attributes["otel.semconv"] = (
        "mixed" if variants == {"stable", "legacy"} else next(iter(variants))
    )
    for rkey, rvalue in resource_attrs.items():
        attributes[f"otel.resource.{rkey}"] = rvalue

    sampling_rate = None
    for skey in ("sampling.rate", "sampling.probability", "sample.rate"):
        if skey in point_attrs:
            with suppress(TypeError, ValueError):
                sampling_rate = float(point_attrs[skey])

    dropped = any(
        _int(dp.get(field) or 0) > 0
        for field in ("droppedAttributesCount", "droppedDataPointsCount")
    )
    issues: list[str] = []
    if _int(dp.get("flags") or 0) & 1:  # FLAG_NO_RECORDED_VALUE
        issues.append("no_recorded_value")

    notes = [
        f"semconv:{name} ({variant})",
        "histogram:explicit-bounds→cumulative-le",
    ]
    if temporality:
        notes.append(f"temporality:{temporality}")

    if "count" not in dp:
        raise _Malformed("missing count")
    measurement = Measurement(
        kind=MeasurementKind.HISTOGRAM,
        unit=metric.get("unit"),
        buckets=_histogram_buckets(dp),
        sum=None if dp.get("sum") is None else float(dp["sum"]),
        count=_int(dp["count"]),
        temporality=temporality,
    )
    if dp.get("min") is not None:
        attributes["otel.min"] = float(dp["min"])
    if dp.get("max") is not None:
        attributes["otel.max"] = float(dp["max"])

    return ImportedItem(
        observation_id=deterministic_observation_id(
            {"resource": resource_attrs, "metric": name, "point": dp}
        ),
        service_id=resolved_service_id,
        operation_id=None,  # never synthesized — unmapped stays UNKNOWN (LAB-006)
        release_id=None,
        window_start=window_start,
        window_end=window_end,
        declared_source_id=None,  # origin is the ingest channel
        declared_quality=QualityState.UNKNOWN,
        sampling_rate=sampling_rate,
        dropped=dropped,
        issues=issues,
        measurement=measurement,
        attributes=attributes,
        transform_notes=notes,
    )
