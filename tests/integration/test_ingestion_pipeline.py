"""Integration: JSONL → pipeline → SQLite; idempotent replay; LAB-001 8/8."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import seed_service_and_source
from typer.testing import CliRunner

from api_sentinel.cli import app
from api_sentinel.domain import (
    FixedClock,
    QualityState,
    SequentialIdFactory,
    TelemetrySource,
    ValidationError,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork, connect
from api_sentinel.ingestion import IngestionPipeline, IngestionPolicy

FIXTURE = (
    Path(__file__).resolve().parents[2] / "lab" / "fixtures" / "lab-001-elevated-latency.jsonl"
)
NOW = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)


def _redactor(text: str) -> str:
    return redact(text).text


_IDS = SequentialIdFactory()  # shared per test run: ids stay unique across pipelines


def _pipeline(db_path: Path, clock: object | None = None, policy: IngestionPolicy | None = None):
    return IngestionPipeline(
        SqliteUnitOfWork(db_path),
        clock or FixedClock(NOW),
        _IDS,
        _redactor,
        policy or IngestionPolicy(),
    )


def _lab_observation_line(obs_id: str, window_start: str, window_end: str) -> dict:
    return {
        "schema": "api-sentinel/RuntimeObservation/v1",
        "observation_id": obs_id,
        "service_id": "svc_lab_payments",
        "operation_id": None,
        "window": {"start": window_start, "end": window_end},
        "source_id": "src_lab_jsonl",
        "quality": "COMPLETE",
        "measurement": {"kind": "counter", "unit": "requests", "value": 10.0},
        "attributes": {"http.route": "/payments/{payment_id}"},
    }


def test_lab001_fixture_ingests_8_of_8(db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    result = _pipeline(db_path).ingest(
        source=source, kind="jsonl", content=FIXTURE.read_bytes()
    )
    assert result.accepted == 8 and result.quarantined == 0 and result.skipped_duplicates == 0
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.count() == 8
        batch = uow.batches.get_by_idempotency_key(result.idempotency_key)
        assert batch is not None and batch.batch_id == result.batch_id
        assert batch.provenance.source_id == "src_lab_jsonl"
        assert batch.provenance.adapter_version is not None
        cp = uow.checkpoints.get("src_lab_jsonl")
        assert cp is not None and cp.checkpoint == "8"


def test_histograms_and_quality_and_route_stored_verbatim(db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    _pipeline(db_path).ingest(source=source, kind="jsonl", content=FIXTURE.read_bytes())
    with SqliteUnitOfWork(db_path) as uow:
        obs = uow.observations.get("obs_0006")
        assert obs is not None
        assert obs.quality == QualityState.COMPLETE
        assert obs.measurement.kind == "histogram" and len(obs.measurement.buckets) == 6
        assert obs.attributes["http.route"] == "/payments/{payment_id}"
        assert obs.operation_id is None  # unmapped operation stays UNKNOWN (LAB-006)


def test_replay_produces_zero_rows_and_identical_aggregates(db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    first = _pipeline(db_path).ingest(source=source, kind="jsonl", content=FIXTURE.read_bytes())
    with SqliteUnitOfWork(db_path) as uow:
        aggregates_before = uow.observations.aggregates()
    # replay — brand-new UoW/pipeline (dedup survives restart, LAB-009)
    second = _pipeline(db_path).ingest(source=source, kind="jsonl", content=FIXTURE.read_bytes())
    assert second.batch_id == first.batch_id
    assert second.accepted == 0 and second.skipped_duplicates == 8
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.count() == 8
        assert uow.observations.aggregates() == aggregates_before


def test_partial_replay_dedups_observations_not_batches(tmp_path: Path, db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    f1 = tmp_path / "part1.jsonl"
    f2 = tmp_path / "part2.jsonl"
    line = json.dumps(
        _lab_observation_line("obs_x", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
    )
    f1.write_text(line + "\n", encoding="utf-8")
    f2.write_text(line + "\n" + line + "\n", encoding="utf-8")  # same obs twice + replay
    r1 = _pipeline(db_path).ingest(source=source, kind="jsonl", content=f1.read_bytes())
    assert r1.accepted == 1
    r2 = _pipeline(db_path).ingest(source=source, kind="jsonl", content=f2.read_bytes())
    assert r2.accepted == 0 and r2.skipped_duplicates == 2
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.count() == 1


def test_conflicting_redelivery_is_quarantined_not_rewritten(db_path: Path, tmp_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    f1 = tmp_path / "a.jsonl"
    f2 = tmp_path / "b.jsonl"
    good = _lab_observation_line("obs_c", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
    conflict = dict(good, measurement={"kind": "counter", "unit": "requests", "value": 99.0})
    f1.write_text(json.dumps(good) + "\n", encoding="utf-8")
    f2.write_text(json.dumps(conflict) + "\n", encoding="utf-8")
    _pipeline(db_path).ingest(source=source, kind="jsonl", content=f1.read_bytes())
    r2 = _pipeline(db_path).ingest(source=source, kind="jsonl", content=f2.read_bytes())
    assert r2.accepted == 0 and r2.quarantined == 1
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.get("obs_c").measurement.value == 10.0  # type: ignore[union-attr]
        assert uow.quarantine.list()[0].reason == "conflicting_observation_id"


def test_three_clocks_are_stored_distinct(db_path: Path, tmp_path: Path) -> None:
    class StepClock:
        def __init__(self) -> None:
            self.t = NOW

        def now(self) -> datetime:
            self.t += timedelta(seconds=1)
            return self.t

    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    f = tmp_path / "one.jsonl"
    f.write_text(
        json.dumps(_lab_observation_line("obs_t", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")),
        encoding="utf-8",
    )
    _pipeline(db_path, clock=StepClock()).ingest(
        source=source, kind="jsonl", content=f.read_bytes()
    )
    with SqliteUnitOfWork(db_path) as uow:
        obs = uow.observations.get("obs_t")
        assert obs is not None
        assert obs.window_start == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)  # event clock
        assert obs.ingest_time != obs.processing_time
        assert obs.window_end < obs.ingest_time


def test_old_data_is_marked_stale_not_complete(db_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    clock = FixedClock(datetime(2026, 1, 1, 6, 0, tzinfo=UTC))  # hours after the window
    _pipeline(db_path, clock=clock).ingest(
        source=source, kind="jsonl", content=FIXTURE.read_bytes()
    )
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.get("obs_0001").quality == QualityState.STALE  # type: ignore[union-attr]


def test_oversized_batch_and_item_cap_rejected(db_path: Path, tmp_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.commit()
    content = FIXTURE.read_bytes()
    with pytest.raises(ValidationError):
        _pipeline(db_path, policy=IngestionPolicy(max_batch_bytes=64)).ingest(
            source=source, kind="jsonl", content=content
        )
    with pytest.raises(ValidationError):
        _pipeline(db_path, policy=IngestionPolicy(max_items=2)).ingest(
            source=source, kind="jsonl", content=content
        )
    assert IngestionPolicy().max_batch_bytes == 10 * 1024 * 1024  # real default ~10 MB
    with SqliteUnitOfWork(db_path) as uow:
        assert uow.observations.count() == 0  # bounded refusal, no partial write


def test_overlapping_source_is_identified_never_summed(db_path: Path, tmp_path: Path) -> None:
    with SqliteUnitOfWork(db_path) as uow:
        source = seed_service_and_source(uow)
        uow.sources.put(
            TelemetrySource(
                source_id="src_gateway", kind="api-gateway",
                service_id="svc_lab_payments", created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )
        uow.commit()
    _pipeline(db_path).ingest(source=source, kind="jsonl", content=FIXTURE.read_bytes())
    # a second source observes the same service+window (gateway vs instrumentation)
    other = _lab_observation_line("obs_gw", "2026-01-01T00:00:30Z", "2026-01-01T00:01:30Z")
    other["source_id"] = "src_gateway"
    f = tmp_path / "gw.jsonl"
    f.write_text(json.dumps(other) + "\n", encoding="utf-8")
    with SqliteUnitOfWork(db_path) as uow:
        gw_source = uow.sources.get("src_gateway")
        assert gw_source is not None
        r2 = _pipeline(db_path).ingest(source=gw_source, kind="jsonl", content=f.read_bytes())
    assert r2.accepted == 1
    assert "src_lab_jsonl" in r2.overlapping_sources  # identified as overlap
    with SqliteUnitOfWork(db_path) as uow:
        # aggregates stay grouped per source — never summed across origins
        pairs = {
            (row["service_id"], row["source_id"]) for row in uow.observations.aggregates()["by"]
        }
        assert ("svc_lab_payments", "src_lab_jsonl") in pairs
        assert ("svc_lab_payments", "src_gateway") in pairs


def test_cli_ingest_end_to_end(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    runner = CliRunner()
    with SqliteUnitOfWork(ws / "sentinel.db") as uow:
        seed_service_and_source(uow)
        uow.commit()
    r = runner.invoke(
        app,
        ["ingest", "--source-id", "src_lab_jsonl", "--file", str(FIXTURE),
         "--workspace", str(ws), "--json"],
    )
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["accepted"] == 8 and out["quarantined"] == 0
    assert out["batch_id"].startswith("batch_")
    # replay through the CLI is idempotent too
    r2 = runner.invoke(
        app,
        ["ingest", "--source-id", "src_lab_jsonl", "--file", str(FIXTURE),
         "--workspace", str(ws), "--json"],
    )
    assert json.loads(r2.stdout)["skipped_duplicates"] == 8


def test_migration_0002_applies_once(tmp_path: Path) -> None:
    conn = connect(tmp_path / "s.db")
    from api_sentinel.infrastructure.storage.migrations import migrate

    assert migrate(conn) == [1, 2]
    assert migrate(conn) == []
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runtime_observations", "ingestion_quarantine"} <= tables
    conn.close()
