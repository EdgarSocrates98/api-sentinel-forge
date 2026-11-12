"""Security: no known secret is persisted through the ingestion path (LAB-010)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from api_sentinel.domain import (
    ApiService,
    FixedClock,
    SequentialIdFactory,
    ServiceEnvironment,
    TelemetrySource,
)
from api_sentinel.infrastructure.security import MASK, redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline, IngestionPolicy

NOW = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)


def _seed(uow: SqliteUnitOfWork) -> TelemetrySource:
    uow.environments.put(ServiceEnvironment(environment_id="env_lab", name="lab", created_at=NOW))
    uow.services.put(
        ApiService(
            service_id="svc_lab_payments",
            service_name="payments-api",
            environment_id="env_lab",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return uow.sources.put(
        TelemetrySource(
            source_id="src_lab_jsonl",
            kind="jsonl-file",
            service_id="svc_lab_payments",
            created_at=NOW,
        )
    )


_IDS = SequentialIdFactory()


def _pipeline(db_path: Path) -> IngestionPipeline:
    return IngestionPipeline(
        SqliteUnitOfWork(db_path),
        FixedClock(NOW),
        _IDS,
        lambda t: redact(t).text,
        IngestionPolicy(),
    )


def test_secret_in_observation_attributes_is_redacted(tmp_path: Path) -> None:
    db_path = tmp_path / "sentinel.db"
    with SqliteUnitOfWork(db_path) as uow:
        source = _seed(uow)
        uow.commit()
    line = {
        "schema": "api-sentinel/RuntimeObservation/v1",
        "observation_id": "obs_secret",
        "service_id": "svc_lab_payments",
        "window": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"},
        "source_id": "src_lab_jsonl",
        "quality": "COMPLETE",
        "measurement": {"kind": "counter", "unit": "requests", "value": 1.0},
        "attributes": {"note": "token=supersecret123", "http.route": "/x"},
    }
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps(line) + "\n", encoding="utf-8")
    result = _pipeline(db_path).ingest(source=source, kind="jsonl", content=f.read_bytes())
    assert result.accepted == 1
    with SqliteUnitOfWork(db_path) as uow:
        obs = uow.observations.get("obs_secret")
        assert obs is not None
        assert "supersecret123" not in json.dumps(obs.attributes)
        assert MASK in obs.attributes["note"]
        # raw row payload must also be clean
        raw = uow.observations.raw_attributes("obs_secret")
        assert "supersecret123" not in raw


def test_quarantine_payload_redacts_before_persist(tmp_path: Path) -> None:
    db_path = tmp_path / "sentinel.db"
    with SqliteUnitOfWork(db_path) as uow:
        source = _seed(uow)
        uow.commit()
    bad = '{"schema":"api-sentinel/RuntimeObservation/v2","key":"password=hunter2"}'
    f = tmp_path / "q.jsonl"
    f.write_text(bad + "\n", encoding="utf-8")
    _pipeline(db_path).ingest(source=source, kind="jsonl", content=f.read_bytes())
    with SqliteUnitOfWork(db_path) as uow:
        stored = uow.quarantine.list()[0].payload
        assert "hunter2" not in stored and MASK in stored
