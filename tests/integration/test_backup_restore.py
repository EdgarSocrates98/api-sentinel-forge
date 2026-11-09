"""LAB-014 — backup → destroy → restore → identical state (docs/13, Cycle 9).

Covers the recovery pyramid layer: the documented backup procedure produces
a verifiable snapshot; a deleted *or* corrupted database restores to the
same tables, and the blob tree comes back byte-identical. `audit_events`
is the only table that legitimately differs — operations journal their own
rows (append-only, never a rewrite).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.application import (
    RegisterService,
    RegisterSource,
    StoreEvidence,
)
from api_sentinel.domain import (
    ConflictError,
    FixedClock,
    SequentialIdFactory,
    StorageError,
)
from api_sentinel.infrastructure.storage import (
    FileBlobStore,
    SqliteUnitOfWork,
)
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.operations.backup import (
    create_backup,
    restore_backup,
    verify_backup,
)
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _seed_workspace(
    ws_root: Path, ids: SequentialIdFactory
) -> tuple[str, str, str]:
    """A real workspace: service + source + observations + evidence blob.

    `ids` is shared with the operations calls so journaled audit ids stay
    unique across backup and restore (deterministic factory = one counter).
    """
    ws = ensure_workspace(Workspace(root=ws_root, scope="project"))
    clock = FixedClock(NOW)
    svc = RegisterService(SqliteUnitOfWork(ws.db_path), clock, ids).execute(
        service_name="payments-api"
    )
    src = RegisterSource(SqliteUnitOfWork(ws.db_path), clock, ids).execute(
        kind="jsonl-file", service_id=svc.service_id
    )
    lines = [
        json.dumps(
            {
                "schema": "api-sentinel/RuntimeObservation/v1",
                "observation_id": f"obs_lab014_{i}",
                "service_id": svc.service_id,
                "operation_id": None,
                "release_id": None,
                "window": {
                    "start": "2026-01-01T11:58:00Z",
                    "end": "2026-01-01T11:59:00Z",
                },
                "source_id": src.source_id,
                "quality": "COMPLETE",
                "measurement": {
                    "kind": "histogram",
                    "unit": "ms",
                    "count": 10,
                    "sum": 950.0,
                    "buckets": [{"le": "+Inf", "count": 10}],
                },
                "attributes": {"http.route": "/payments"},
            }
        )
        for i in range(3)
    ]
    pipeline = IngestionPipeline(
        SqliteUnitOfWork(ws.db_path), clock, ids, lambda t: t
    )
    pipeline.ingest(
        source=src, kind="jsonl", content="\n".join(lines).encode()
    )
    evidence = StoreEvidence(
        SqliteUnitOfWork(ws.db_path), clock, ids, FileBlobStore(ws.blobs_dir)
    ).execute(
        kind="metric-export",
        content=b"lab014 evidence payload",
        source_id=src.source_id,
    )
    assert evidence.blob_ref is not None
    return svc.service_id, src.source_id, evidence.blob_ref


def _dump_tables(db_path: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
    conn = sqlite3.connect(str(db_path))
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            t: tuple(
                sorted(
                    conn.execute(f"SELECT * FROM {t}").fetchall(),  # noqa: S608
                    key=repr,
                )
            )
            for t in tables
        }
    finally:
        conn.close()


def _audit_actions(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            r[0]
            for r in conn.execute(
                "SELECT action FROM audit_events ORDER BY at, audit_id"
            )
        ]
    finally:
        conn.close()


def _destroy_workspace(ws: Workspace) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(ws.db_path) + suffix)
        if p.exists():
            p.unlink()
    if ws.blobs_dir.is_dir():
        shutil.rmtree(ws.blobs_dir)


class TestBackupRestore:
    def test_round_trip_restores_identical_state(self, tmp_path: Path) -> None:
        ids = SequentialIdFactory()
        ws = Workspace(root=tmp_path / "ws", scope="project")
        service_id, source_id, blob_ref = _seed_workspace(ws.root, ids)
        before = _dump_tables(ws.db_path)
        assert before["runtime_observations"]

        backup = create_backup(
            ws,
            clock=FixedClock(NOW),
            ids=ids,
        )
        manifest = verify_backup(backup.backup_dir)
        assert manifest["schema"] == "api-sentinel/BackupManifest/v1"
        assert manifest["tables"]["runtime_observations"] == 3
        assert "sentinel.db" in manifest["checksums"]

        _destroy_workspace(ws)
        assert not ws.db_path.exists()
        assert not ws.blobs_dir.is_dir()

        result = restore_backup(
            backup.backup_dir,
            ws,
            clock=FixedClock(NOW),
            ids=ids,
        )
        assert result.integrity == "ok"
        assert result.blobs_restored == 1

        after = _dump_tables(ws.db_path)
        for table, rows in before.items():
            if table == "audit_events":
                continue  # operations journal rows are legitimately new
            assert after[table] == rows, f"table {table} differs after restore"
        assert set(after) == set(before)
        # the blob came back byte-identical and still resolves
        blob = FileBlobStore(ws.blobs_dir).get(blob_ref)
        assert blob == b"lab014 evidence payload"
        with SqliteUnitOfWork(ws.db_path) as uow:
            assert uow.services.get(service_id) is not None
            assert uow.sources.get(source_id) is not None
            assert uow.observations.count() == 3
        actions = _audit_actions(ws.db_path)
        assert "operations.backup" in actions
        assert "operations.restore" in actions

    def test_restore_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        ids = SequentialIdFactory()
        ws = Workspace(root=tmp_path / "ws", scope="project")
        _seed_workspace(ws.root, ids)
        backup = create_backup(
            ws, clock=FixedClock(NOW), ids=ids
        )
        with pytest.raises(ConflictError):
            restore_backup(
                backup.backup_dir,
                ws,
                clock=FixedClock(NOW),
                ids=ids,
            )

    def test_restore_with_force_moves_live_db_aside(self, tmp_path: Path) -> None:
        ids = SequentialIdFactory()
        ws = Workspace(root=tmp_path / "ws", scope="project")
        _seed_workspace(ws.root, ids)
        backup = create_backup(
            ws, clock=FixedClock(NOW), ids=ids
        )
        result = restore_backup(
            backup.backup_dir,
            ws,
            force=True,
            clock=FixedClock(NOW),
            ids=ids,
        )
        assert result.integrity == "ok"
        # displaced file preserved, never deleted
        assert result.replaced_paths
        assert all(p.exists() for p in result.replaced_paths)
        assert _dump_tables(ws.db_path)["runtime_observations"]

    def test_corrupted_backup_is_refused(self, tmp_path: Path) -> None:
        ids = SequentialIdFactory()
        ws = Workspace(root=tmp_path / "ws", scope="project")
        _seed_workspace(ws.root, ids)
        backup = create_backup(
            ws, clock=FixedClock(NOW), ids=ids
        )
        db_copy = backup.backup_dir / "sentinel.db"
        payload = bytearray(db_copy.read_bytes())
        payload[-17] ^= 0xFF
        db_copy.write_bytes(bytes(payload))
        with pytest.raises(StorageError):
            verify_backup(backup.backup_dir)
        with pytest.raises(StorageError):
            restore_backup(
                backup.backup_dir,
                Workspace(root=tmp_path / "ws2", scope="project"),
                clock=FixedClock(NOW),
                ids=SequentialIdFactory(),
            )
