"""Integration: invalid payloads are quarantined, never into aggregates."""

import json
from datetime import UTC, datetime
from pathlib import Path

from api_sentinel.domain import FixedClock, SequentialIdFactory
from api_sentinel.infrastructure.security import MASK, redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline, IngestionPolicy
from conftest import seed_service_and_source

NOW = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)


_IDS = SequentialIdFactory()


def _pipeline(db_path: Path) -> IngestionPipeline:
    return IngestionPipeline(
        SqliteUnitOfWork(db_path),
        FixedClock(NOW),
        _IDS,
        lambda t: redact(t).text,
        IngestionPolicy(),
    )


def _good_line(obs_id: str = "obs_ok", **overrides: object) -> dict:
    base = {
        "schema": "api-sentinel/RuntimeObservation/v1",
        "observation_id": obs_id,
        "service_id": "svc_lab_payments",
        "operation_id": None,
        "window": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-01T00:01:00Z"},
        "source_id": "src_lab_jsonl",
        "quality": "COMPLETE",
        "measurement": {"kind": "counter", "unit": "requests", "value": 3.0},
        "attributes": {},
    }
    base.update(overrides)
    return base


def _file(tmp_path: Path, *lines: str) -> bytes:
    f = tmp_path / "batch.jsonl"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f.read_bytes()


def test_malformed_and_bad_schema_lines_quarantined_rest_accepted(
    db_path: Path, tmp_path: Path
) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    content = _file(
        tmp_path,
        json.dumps(_good_line()),
        "{not valid json",
        json.dumps({"schema": "api-sentinel/RuntimeObservation/v99", "observation_id": "o"}),
        json.dumps(dict(_good_line("obs_extra"), bogus_field=1)),
        json.dumps(["not", "an", "object"]),
        json.dumps(_good_line("obs_ghost", service_id="svc_does_not_exist")),
        json.dumps(_good_line("obs_origin", source_id="src_someone_else")),
    )
    result = _pipeline(db_path).ingest(source=source, kind="jsonl", content=content)
    assert result.accepted == 1 and result.quarantined == 6
    with SqliteUnitOfWork(db_path) as uow:
        entries = uow.quarantine.list()
        assert {e.reason for e in entries} == {
            "invalid_json",
            "unknown_schema",
            "schema_validation",
            "not_an_object",
            "unresolved_service",
            "origin_mismatch",
        }
        assert all(e.batch_id == result.batch_id for e in entries)
        # quarantine cannot contaminate aggregates
        assert uow.observations.count() == 1
        assert uow.observations.aggregates()["total"] == 1


def test_quarantine_is_inspectable_and_payload_bounded(db_path: Path, tmp_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    huge = json.dumps({"schema": "api-sentinel/RuntimeObservation/v2", "pad": "x" * 9000})
    result = _pipeline(db_path).ingest(
        source=source, kind="jsonl", content=_file(tmp_path, huge)
    )
    assert result.quarantined == 1
    with SqliteUnitOfWork(db_path) as uow:
        entry = uow.quarantine.list()[0]
        assert entry.reason == "unknown_schema"
        assert entry.payload_truncated
        assert len(entry.payload) <= 4096
        assert entry.item_id and entry.created_at


def test_quarantined_rows_never_enter_observations(db_path: Path, tmp_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    only_bad = _file(tmp_path, '{"schema":"api-sentinel/RuntimeObservation/v9"}')
    result = _pipeline(db_path).ingest(source=source, kind="jsonl", content=only_bad)
    assert result.accepted == 0 and result.quarantined == 1
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.count() == 0
        assert uow.observations.aggregates()["total"] == 0


def test_quarantine_payload_is_redacted(db_path: Path, tmp_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    leaky_line = '{"schema":"api-sentinel/RuntimeObservation/v9","auth":"token=supersecret123"}'
    _pipeline(db_path).ingest(
        source=source, kind="jsonl", content=_file(tmp_path, leaky_line)
    )
    with SqliteUnitOfWork(db_path) as uow:
        payload = uow.quarantine.list()[0].payload
        assert "supersecret123" not in payload and MASK in payload
