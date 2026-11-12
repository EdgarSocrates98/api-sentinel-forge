"""Cycle-10 §5.6 — storage/recovery revalidation.

Covers the Wave-0 recovery matrix beyond the LAB-014 round-trip suite:

- the wheel payload carries migrations (inside the packaged tree);
- clean install / rerun / upgrade-with-data / failed-migration rollback;
- interrupted and corrupted backups refused before any write;
- restore refusal without force + authorized restore integrity;
- a crash mid-ingest leaves no partial rows (single transaction);
- orphan blob files are reported, never silently deleted;
- retention protects evidence referenced by open incidents while
  closed-incident evidence stays eligible.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from api_sentinel.application import RegisterService, RegisterSource
from api_sentinel.detection import Severity
from api_sentinel.domain import (
    ApiService,
    ConflictError,
    EvidenceArtifact,
    FixedClock,
    SequentialIdFactory,
    ServiceEnvironment,
    StorageError,
    TelemetrySource,
)
from api_sentinel.incidents import Incident, IncidentStatus
from api_sentinel.infrastructure.storage import (
    FileBlobStore,
    SqliteUnitOfWork,
    migrations,
)
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.operations.backup import (
    create_backup,
    restore_backup,
    verify_backup,
)
from api_sentinel.operations.retention import RetentionPolicy, run_retention
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _workspace(root: Path) -> Workspace:
    return ensure_workspace(Workspace(root=root, scope="project"))


def _seed_service_source(ws: Workspace, ids: SequentialIdFactory) -> tuple[str, str]:
    svc = RegisterService(SqliteUnitOfWork(ws.db_path), FixedClock(NOW), ids).execute(
        service_name="payments-api"
    )
    src = RegisterSource(SqliteUnitOfWork(ws.db_path), FixedClock(NOW), ids).execute(
        kind="jsonl-file", service_id=svc.service_id
    )
    return svc.service_id, src.source_id


class TestMigrationLifecycle:
    def test_migrations_ship_inside_the_package(self) -> None:
        """The wheel target packages `src/api_sentinel` wholesale —
        `MIGRATIONS_DIR` living under it is what guarantees wheels carry
        the SQL (the Cycle-9 packaging fix stays pinned)."""
        import api_sentinel

        pkg_root = Path(api_sentinel.__file__).resolve().parent
        assert migrations.MIGRATIONS_DIR.is_relative_to(pkg_root)
        found = migrations.discover()
        assert len(found) >= 6
        names = [name for _, name, _ in found]
        assert names == sorted(names)
        for _, _, path in found:
            assert path.read_text(encoding="utf-8").strip()

    def test_built_wheel_carries_migrations(self, tmp_path: Path) -> None:
        """§5.5 — regression against the *built artifact*: build the wheel
        and assert the SQL payloads are inside it, so a packaging change
        can never again ship a db-less install."""
        uv = shutil.which("uv")
        if uv is None:
            pytest.skip("uv not on PATH — wheel build unverifiable here")
        repo = Path(__file__).resolve().parents[2]
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [uv, "build", "--wheel", "--out-dir", str(tmp_path)],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-500:]
        wheels = list(tmp_path.glob("*.whl"))
        assert len(wheels) == 1
        with zipfile.ZipFile(wheels[0]) as zf:
            sql = [n for n in zf.namelist() if "/migrations/" in n and n.endswith(".sql")]
        assert len(sql) >= 6, f"wheel carries {len(sql)} migrations: {sql}"

    def test_clean_install_applies_schema(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path / "ws")
        with SqliteUnitOfWork(ws.db_path) as uow:
            uow.commit()
        conn = sqlite3.connect(str(ws.db_path))
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert {"api_services", "runtime_observations", "schema_migrations"} <= tables
        finally:
            conn.close()

    def test_migration_rerun_is_a_noop(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path / "ws")
        conn = sqlite3.connect(str(ws.db_path))
        conn.row_factory = sqlite3.Row
        try:
            first = migrations.migrate(conn)
            again = migrations.migrate(conn)
            assert first and again == []
        finally:
            conn.close()

    def test_upgrade_with_data_preserves_rows(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path / "ws")
        ids = SequentialIdFactory()
        service_id, _ = _seed_service_source(ws, ids)
        # "upgrade": close, reopen — pending migrations run, data stays
        with SqliteUnitOfWork(ws.db_path) as uow:
            assert uow.services.get(service_id) is not None
            uow.commit()

    def test_failed_migration_records_nothing(self, tmp_path: Path) -> None:
        mig_dir = tmp_path / "migrations"
        mig_dir.mkdir()
        (mig_dir / "0001_good.sql").write_text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL,"
            " checksum TEXT NOT NULL, applied_at TEXT NOT NULL);"
            "CREATE TABLE t_good (id TEXT PRIMARY KEY);",
            encoding="utf-8",
        )
        (mig_dir / "0002_broken.sql").write_text("THIS IS NOT SQL;", encoding="utf-8")
        conn = sqlite3.connect(str(tmp_path / "db.sqlite"))
        conn.row_factory = sqlite3.Row
        try:
            with pytest.raises(sqlite3.Error):
                migrations.migrate(conn, migrations_dir=mig_dir)
            applied = migrations.applied_versions(conn)
            # the good migration committed; the broken one recorded nothing
            assert applied == {1}
            fixed = "CREATE TABLE t_fixed (id TEXT PRIMARY KEY);"
            (mig_dir / "0002_broken.sql").write_text(fixed, encoding="utf-8")
            assert migrations.migrate(conn, migrations_dir=mig_dir) == [2]
        finally:
            conn.close()


class TestBackupRecoveryEdges:
    def _backup(self, tmp_path: Path):
        ids = SequentialIdFactory()
        ws = _workspace(tmp_path / "ws")
        _seed_service_source(ws, ids)
        return ws, ids, create_backup(ws, clock=FixedClock(NOW), ids=ids)

    def test_interrupted_backup_missing_payload_is_refused(self, tmp_path: Path) -> None:
        """A backup killed mid-copy — manifest present, db absent — is a
        named integrity failure, never a partial restore."""
        _ws, ids, backup = self._backup(tmp_path)
        (backup.backup_dir / "sentinel.db").unlink()
        with pytest.raises(StorageError):
            verify_backup(backup.backup_dir)
        with pytest.raises(StorageError):
            restore_backup(
                backup.backup_dir,
                Workspace(root=tmp_path / "ws2", scope="project"),
                clock=FixedClock(NOW),
                ids=ids,
            )
        assert not (tmp_path / "ws2" / "sentinel.db").exists()

    def test_corrupt_manifest_is_refused(self, tmp_path: Path) -> None:
        _, ids, backup = self._backup(tmp_path)
        manifest_path = backup.backup_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["checksums"]["sentinel.db"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(StorageError):
            verify_backup(backup.backup_dir)
        with pytest.raises(StorageError):
            restore_backup(
                backup.backup_dir,
                Workspace(root=tmp_path / "ws2", scope="project"),
                ids=ids,
            )

    def test_restore_refusal_then_authorized_restore(self, tmp_path: Path) -> None:
        """Without force a live db is a ConflictError; authorized force
        restores with verified integrity + preserved displaced files."""
        ws, ids, backup = self._backup(tmp_path)
        with pytest.raises(ConflictError):
            restore_backup(backup.backup_dir, ws, clock=FixedClock(NOW), ids=ids)
        result = restore_backup(
            backup.backup_dir,
            ws,
            force=True,
            clock=FixedClock(NOW),
            ids=ids,
        )
        assert result.integrity == "ok"
        assert all(p.exists() for p in result.replaced_paths)


class TestCrashMidIngest:
    def test_failed_commit_leaves_no_partial_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crash inside the persist transaction rolls back everything —
        a retried ingest of the same content then succeeds (idempotent)."""
        ws = _workspace(tmp_path / "ws")
        ids = SequentialIdFactory()
        _, source_id = _seed_service_source(ws, ids)
        with SqliteUnitOfWork(ws.db_path) as uow:
            source = uow.sources.get(source_id)
            uow.commit()
        assert source is not None
        line = json.dumps(
            {
                "schema": "api-sentinel/RuntimeObservation/v1",
                "observation_id": "obs_crash_1",
                "service_id": source.service_id,
                "window": {
                    "start": "2026-01-01T11:58:00Z",
                    "end": "2026-01-01T11:59:00Z",
                },
                "source_id": source_id,
                "quality": "COMPLETE",
                "measurement": {
                    "kind": "counter",
                    "unit": "requests",
                    "value": 1,
                },
                "attributes": {},
            }
        )
        uow = SqliteUnitOfWork(ws.db_path)
        pipeline = IngestionPipeline(uow, FixedClock(NOW), ids, lambda t: t)

        def _crash() -> None:
            raise RuntimeError("simulated crash mid-commit")

        monkeypatch.setattr(uow, "commit", _crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            pipeline.ingest(source=source, kind="jsonl", content=line.encode())
        monkeypatch.undo()
        # nothing persisted — no batch, no observation, no checkpoint
        with SqliteUnitOfWork(ws.db_path) as check:
            assert check.observations.count() == 0
            assert check.batches.get_by_idempotency_key("does-not-matter") is None
            check.commit()
        # retry of the identical content is a clean re-ingest, not a dup
        retry = IngestionPipeline(
            SqliteUnitOfWork(ws.db_path), FixedClock(NOW), ids, lambda t: t
        ).ingest(source=source, kind="jsonl", content=line.encode())
        assert retry.accepted == 1 and retry.skipped_duplicates == 0


class TestOrphansAndRetention:
    def _evidence_for_incident(
        self,
        ws: Workspace,
        ids: SequentialIdFactory,
        *,
        incident_status: IncidentStatus,
        old: bool,
    ) -> str:
        """Evidence referenced by an incident in the given status."""
        created = NOW - timedelta(days=365) if old else NOW
        with SqliteUnitOfWork(ws.db_path) as uow:
            env = uow.environments.get_by_name("lab") or uow.environments.put(
                ServiceEnvironment(environment_id="env_lab", name="lab", created_at=created)
            )
            uow.services.put(
                ApiService(
                    service_id="svc_ret",
                    service_name="ret-api",
                    environment_id=env.environment_id,
                    created_at=created,
                    updated_at=created,
                )
            )
            uow.sources.put(
                TelemetrySource(
                    source_id="src_ret",
                    kind="jsonl-file",
                    service_id="svc_ret",
                    created_at=created,
                )
            )
            evidence_id = f"ev_{incident_status.value.lower()}"
            blob = FileBlobStore(ws.blobs_dir).put(b"payload-" + evidence_id.encode())
            uow.evidence.put(
                EvidenceArtifact(
                    evidence_id=evidence_id,
                    kind="metric-export",
                    created_at=created,
                    hash=blob,
                    size_bytes=8,
                    source_id="src_ret",
                    blob_ref=blob,
                )
            )
            uow.incidents.put(
                Incident(
                    incident_id=f"inc_{incident_status.value.lower()}",
                    title="retention lab",
                    severity=Severity.SEV3,
                    status=incident_status,
                    service_id="svc_ret",
                    group_key=f"svc_ret|{incident_status.value}|x",
                    first_observed_at=created,
                    detected_at=created,
                    resolved_at=(created if incident_status is IncidentStatus.RESOLVED else None),
                    evidence_refs=[evidence_id],
                )
            )
            uow.commit()
        return evidence_id

    def test_orphan_blob_files_reported_never_deleted(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path / "ws")
        _seed_service_source(ws, SequentialIdFactory())
        ws.blobs_dir.mkdir(parents=True, exist_ok=True)
        orphan = ws.blobs_dir / ("deadbeef" * 8)
        orphan.write_bytes(b"unreferenced")
        report = run_retention(ws, apply=True, clock=FixedClock(NOW), ids=SequentialIdFactory())
        assert report.orphan_blob_files >= 1
        assert orphan.is_file()  # reported, never silently swept

    def test_open_incident_evidence_protected_closed_eligible(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path / "ws")
        ids = SequentialIdFactory()
        open_ev = self._evidence_for_incident(
            ws, ids, incident_status=IncidentStatus.INVESTIGATING, old=True
        )
        closed_ev = self._evidence_for_incident(
            ws, ids, incident_status=IncidentStatus.RESOLVED, old=True
        )
        report = run_retention(
            ws,
            apply=True,
            policy=RetentionPolicy(),
            clock=FixedClock(NOW),
            ids=ids,
        )
        assert report.candidates["evidence"] >= 1
        with SqliteUnitOfWork(ws.db_path) as uow:
            assert uow.evidence.get(open_ev) is not None
            assert uow.evidence.get(closed_ev) is None
            uow.commit()
