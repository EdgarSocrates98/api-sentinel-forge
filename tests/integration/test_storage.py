import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.application import ListServices, RegisterService
from api_sentinel.domain import (
    ApiService,
    IngestionCheckpoint,
    ObservationBatch,
    Provenance,
    QualityState,
    SequentialIdFactory,
    ServiceEnvironment,
    SignalType,
    SystemClock,
    TelemetrySource,
)
from api_sentinel.infrastructure.storage import (
    FileBlobStore,
    SqliteUnitOfWork,
    connect,
    migrate,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _uow(tmp_path: Path) -> SqliteUnitOfWork:
    return SqliteUnitOfWork(tmp_path / "sentinel.db")


def _seed_service_and_source(uow: SqliteUnitOfWork) -> None:
    env = uow.environments.get_by_name("e") or uow.environments.put(
        ServiceEnvironment(environment_id="env_1", name="e", created_at=NOW)
    )
    uow.services.put(
        ApiService(
            service_id="svc_1",
            service_name="s",
            environment_id=env.environment_id,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.sources.put(
        TelemetrySource(
            source_id="src_1", kind="jsonl-file", service_id="svc_1", created_at=NOW
        )
    )


def test_migrations_apply_once_and_checksum(tmp_path: Path) -> None:
    conn = connect(tmp_path / "s.db")
    first = migrate(conn)
    second = migrate(conn)
    assert first == [1, 2, 3, 4, 5] and second == []
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    assert len(rows) == 5 and all(len(r["checksum"]) == 64 for r in rows)
    conn.close()


def test_pragmas_wal_and_foreign_keys(tmp_path: Path) -> None:
    conn = connect(tmp_path / "s.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_register_service_is_idempotent(tmp_path: Path) -> None:
    uc = RegisterService(_uow(tmp_path), SystemClock(), SequentialIdFactory())
    a = uc.execute(service_name="payments-api")
    b = uc.execute(service_name="payments-api")
    assert a.service_id == b.service_id
    assert len(ListServices(_uow(tmp_path)).execute()) == 1


def test_batch_reprocessing_never_duplicates(tmp_path: Path) -> None:
    batch = ObservationBatch(
        batch_id="b1",
        provenance=Provenance(source_id="src_1", observed_at=NOW, received_at=NOW),
        signal_types=[SignalType.COUNTER],
        quality=QualityState.COMPLETE,
        item_count=3,
        idempotency_key="key-1",
    )
    with _uow(tmp_path) as uow:
        _seed_service_and_source(uow)
        uow.batches.put(batch)
        uow.batches.put(batch)  # replay
        uow.commit()
    with _uow(tmp_path) as uow:
        same = uow.batches.get_by_idempotency_key("key-1")
        assert same is not None and same.batch_id == "b1"


def test_audit_is_append_only() -> None:
    from api_sentinel.infrastructure.storage.repositories import _AuditRepo

    public = [m for m in dir(_AuditRepo) if not m.startswith("_")]
    assert set(public) == {"append", "list"}


def test_checkpoint_upsert(tmp_path: Path) -> None:
    with _uow(tmp_path) as uow:
        _seed_service_and_source(uow)
        uow.checkpoints.put(IngestionCheckpoint(source_id="src_1", checkpoint="10", updated_at=NOW))
        uow.checkpoints.put(IngestionCheckpoint(source_id="src_1", checkpoint="25", updated_at=NOW))
        uow.commit()
    with _uow(tmp_path) as uow:
        got = uow.checkpoints.get("src_1")
        assert got is not None and got.checkpoint == "25"


def test_blob_store_content_addressed(tmp_path: Path) -> None:
    store = FileBlobStore(tmp_path / "blobs")
    ref1 = store.put(b"evidence")
    ref2 = store.put(b"evidence")
    assert ref1 == ref2 and ref1.startswith("sha256:")
    assert store.get(ref1) == b"evidence"


def test_fk_violation_rejected(tmp_path: Path) -> None:
    with _uow(tmp_path) as uow, pytest.raises(sqlite3.IntegrityError):
        uow.sources.put(
            TelemetrySource(
                source_id="src_x", kind="jsonl-file", service_id="ghost", created_at=NOW
            )
        )
        uow.commit()
