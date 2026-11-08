"""Contract: documented OTLP JSON subset → correct normalized observations."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.domain import (
    ApiService,
    FixedClock,
    QualityState,
    SequentialIdFactory,
    ServiceEnvironment,
    TelemetrySource,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline, IngestionPolicy
from api_sentinel.ingestion.importers import detect_kind, parse_otlp_json

NOW = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)


def _seed(uow: SqliteUnitOfWork) -> TelemetrySource:
    uow.environments.put(
        ServiceEnvironment(environment_id="env_lab", name="lab", created_at=NOW)
    )
    uow.services.put(
        ApiService(
            service_id="svc_lab_payments", service_name="payments-api",
            environment_id="env_lab", created_at=NOW, updated_at=NOW,
        )
    )
    return uow.sources.put(
        TelemetrySource(
            source_id="src_otlp", kind="otlp-json",
            service_id="svc_lab_payments", created_at=NOW,
        )
    )


def _otlp_doc(
    metric_name: str = "http.server.request.duration",
    attrs: list[dict] | None = None,
    unit: str = "s",
    temporality: int = 2,
) -> dict:
    if attrs is None:
        attrs = [
            {"key": "http.request.method", "value": {"stringValue": "POST"}},
            {"key": "http.response.status_code", "value": {"intValue": "201"}},
            {"key": "http.route", "value": {"stringValue": "/payments/{payment_id}"}},
        ]
    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "payments-api"}}
                    ]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "otel.scope", "version": "1.0"},
                        "metrics": [
                            {
                                "name": metric_name,
                                "unit": unit,
                                "histogram": {
                                    "aggregationTemporality": temporality,
                                    "dataPoints": [
                                        {
                                            "attributes": attrs,
                                            "startTimeUnixNano": "1767225600000000000",
                                            "timeUnixNano": "1767225660000000000",
                                            "count": "120",
                                            "sum": 14.4,
                                            "explicitBounds": [0.05, 0.1, 0.2, 0.5, 1.0],
                                            "bucketCounts": ["10", "45", "40", "12", "3", "10"],
                                        }
                                    ],
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _resolver(name: str):
    return {"payments-api": "svc_lab_payments"}.get(name)


def test_otlp_histogram_maps_to_canonical_observation() -> None:
    outcome = parse_otlp_json(json.dumps(_otlp_doc()).encode(), resolve_service=_resolver)
    assert not outcome.quarantine and len(outcome.items) == 1
    item = outcome.items[0]
    assert item.service_id == "svc_lab_payments"
    assert item.measurement.kind == "histogram"
    assert item.measurement.unit == "s"
    assert item.measurement.temporality == "cumulative"
    # OTel explicit bounds + per-bucket counts → cumulative le form
    assert [b["le"] for b in item.measurement.buckets] == [0.05, 0.1, 0.2, 0.5, 1.0, "+Inf"]
    assert [b["count"] for b in item.measurement.buckets] == [10, 55, 95, 107, 110, 120]
    assert item.measurement.count == 120 and item.measurement.sum == 14.4
    assert item.window_start == datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert item.window_end == datetime(2026, 1, 1, 0, 1, 0, tzinfo=UTC)
    assert item.attributes["http.request.method"] == "POST"
    assert item.attributes["http.response.status_code"] == 201
    assert item.attributes["otel.semconv"] == "stable"
    assert item.operation_id is None  # never synthesized (LAB-006)


def test_legacy_semconv_variant_recorded() -> None:
    doc = _otlp_doc(
        metric_name="http.server.duration",
        attrs=[
            {"key": "http.method", "value": {"stringValue": "GET"}},
            {"key": "http.status_code", "value": {"intValue": "200"}},
        ],
    )
    outcome = parse_otlp_json(json.dumps(doc).encode(), resolve_service=_resolver)
    item = outcome.items[0]
    assert item.attributes["http.request.method"] == "GET"
    assert item.attributes["http.response.status_code"] == 200
    assert item.attributes["otel.semconv"] == "legacy"
    assert any("http.server.duration" in n for n in item.transform_notes)


def test_delta_temporality_carried_explicitly() -> None:
    outcome = parse_otlp_json(
        json.dumps(_otlp_doc(temporality=1)).encode(), resolve_service=_resolver
    )
    assert outcome.items[0].measurement.temporality == "delta"


def test_detect_kind_sniffs_resource_metrics() -> None:
    assert detect_kind(json.dumps(_otlp_doc()).encode()) == "otlp-json"
    assert detect_kind(b'{"schema":"x"}\n{"schema":"y"}\n') == "jsonl"


def test_unsupported_metric_and_shape_quarantined() -> None:
    doc = _otlp_doc(metric_name="jvm.memory.used")
    outcome = parse_otlp_json(json.dumps(doc).encode(), resolve_service=_resolver)
    assert outcome.quarantine[0].reason == "unsupported_metric"

    shapeless = parse_otlp_json(b'{"traces":[]}', resolve_service=_resolver)
    assert shapeless.quarantine[0].reason == "unsupported_shape"

    non_histogram = _otlp_doc()
    metric = non_histogram["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]
    metric["sum"] = metric.pop("histogram")
    outcome = parse_otlp_json(json.dumps(non_histogram).encode(), resolve_service=_resolver)
    assert outcome.quarantine[0].reason == "unsupported_datatype"


def test_otlp_end_to_end_seconds_become_ms(tmp_path: Path) -> None:
    db_path = tmp_path / "sentinel.db"
    with SqliteUnitOfWork(db_path) as uow:
        source = _seed(uow)
        uow.commit()
    pipeline = IngestionPipeline(
        SqliteUnitOfWork(db_path),
        FixedClock(datetime(2026, 1, 1, 0, 2, tzinfo=UTC)),  # window end is fresh
        SequentialIdFactory(),
        lambda t: redact(t).text,
        IngestionPolicy(),
    )
    result = pipeline.ingest(
        source=source, kind="otlp-json", content=json.dumps(_otlp_doc()).encode()
    )
    assert result.accepted == 1 and result.quarantined == 0
    with SqliteUnitOfWork(db_path) as uow:
        obs = next(iter(uow.observations.list_by_batch(result.batch_id)))
        assert obs.measurement.unit == "ms"
        assert obs.measurement.sum == pytest.approx(14400.0)
        assert obs.measurement.buckets[0].le == 50.0
        # OTLP carries no producer quality claim → honest UNKNOWN, never upgraded
        assert obs.quality == QualityState.UNKNOWN
        assert obs.service_id == "svc_lab_payments"
