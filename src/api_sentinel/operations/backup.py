"""Workspace backup/restore — SQLite `.backup` + blobs + verifiable manifest.

The backup is a directory: `sentinel.db` (online-safe `Connection.backup`,
consistent under WAL), the `blobs/` tree copied byte-for-byte, `config.yaml`
when present, and `manifest.json` carrying per-table row counts and SHA-256
checksums. Staging lands in `<name>.tmp` and is renamed once — a backup is
either complete or absent, never half-written.

Restore verifies every checksum before touching the workspace, refuses to
overwrite live data unless `force=True` (the displaced files are moved aside,
never deleted), and finishes with `PRAGMA integrity_check` +
`foreign_key_check`. Both directions journal an `audit_events` row — the
audit trail stays append-only, even across a restore.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api_sentinel import __version__
from api_sentinel.domain import (
    Clock,
    ConflictError,
    IdFactory,
    NotFoundError,
    StorageError,
    SystemClock,
    ValidationError,
)
from api_sentinel.infrastructure.storage.db import connect
from api_sentinel.workspace import Workspace, ensure_workspace

MANIFEST_SCHEMA = "api-sentinel/BackupManifest/v1"
MANIFEST_NAME = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        )
    return counts


def _stamp(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _free_path(candidate: Path) -> Path:
    if not candidate.exists():
        return candidate
    for n in range(1, 100):
        alt = candidate.with_name(f"{candidate.name}-{n}")
        if not alt.exists():
            return alt
    raise ConflictError(f"no free path under {candidate.parent}")


@dataclass(frozen=True)
class BackupResult:
    """What a backup run honestly produced."""

    backup_dir: Path
    manifest: dict[str, Any] = field(default_factory=dict)


def _journal(
    ws: Workspace,
    clock: Clock,
    ids: IdFactory | None,
    *,
    action: str,
    details: dict[str, str],
) -> None:
    """Append an operations audit row — append-only, best-effort is not
    acceptable here: a failed audit write fails the operation loudly."""
    if ids is None or not ws.db_path.is_file():
        return
    conn = connect(ws.db_path)
    try:
        conn.execute(
            "INSERT INTO audit_events"
            " (audit_id, at, actor, action, target_type, target_id, details)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                ids.new("aud"),
                clock.now().isoformat(),
                "operations",
                action,
                "workspace",
                str(ws.root),
                json.dumps(details),
            ),
        )
    finally:
        conn.close()


def create_backup(
    ws: Workspace,
    *,
    dest_parent: Path | None = None,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
) -> BackupResult:
    """Snapshot db + blobs into a verifiable backup directory.

    The audit row is journaled *before* the snapshot so the backup itself
    records that it was taken. Returns the completed backup directory.
    """
    clock = clock or SystemClock()
    if not ws.db_path.is_file():
        raise NotFoundError(f"database not found: {ws.db_path}")

    now = clock.now()
    parent = dest_parent or (ws.root / "backups")
    parent.mkdir(parents=True, exist_ok=True)
    final = _free_path(parent / f"backup-{_stamp(now)}")
    staging = final.with_name(final.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    _journal(
        ws,
        clock,
        ids,
        action="operations.backup",
        details={"backup_dir": str(final)},
    )

    checksums: dict[str, str] = {}
    # --- database: online-safe snapshot (WAL-consistent) ---
    src = connect(ws.db_path)
    try:
        tables = _table_counts(src)
        dst = connect(staging / "sentinel.db")
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    checksums["sentinel.db"] = _sha256(staging / "sentinel.db")

    # --- evidence blobs: immutable content-addressed files ---
    blob_count = 0
    blobs_out = staging / "blobs"
    blobs_out.mkdir()
    if ws.blobs_dir.is_dir():
        for blob in sorted(ws.blobs_dir.iterdir()):
            if not blob.is_file():
                continue
            copied = blobs_out / blob.name
            shutil.copyfile(blob, copied)
            checksums[f"blobs/{blob.name}"] = _sha256(copied)
            blob_count += 1

    # --- workspace configuration (restored only when absent) ---
    if ws.config_path.is_file():
        shutil.copyfile(ws.config_path, staging / "config.yaml")
        checksums["config.yaml"] = _sha256(staging / "config.yaml")

    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "app_version": __version__,
        "at": now.astimezone(UTC).isoformat(),
        "tables": tables,
        "blob_count": blob_count,
        "checksums": checksums,
    }
    (staging / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    staging.rename(final)
    return BackupResult(backup_dir=final, manifest=manifest)


def read_manifest(backup_dir: Path) -> dict[str, Any]:
    path = backup_dir / MANIFEST_NAME
    if not path.is_file():
        raise NotFoundError(f"backup manifest not found: {path}")
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValidationError(
            f"unknown backup manifest schema: {manifest.get('schema')!r}",
            details={"expected": MANIFEST_SCHEMA},
        )
    return manifest


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    """Re-verify every checksum in the manifest — restore never trusts."""
    manifest = read_manifest(backup_dir)
    mismatched: list[str] = []
    missing: list[str] = []
    for rel, expected in sorted(manifest["checksums"].items()):
        path = backup_dir / rel
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256(path) != expected:
            mismatched.append(rel)
    if missing or mismatched:
        raise StorageError(
            "backup integrity check failed",
            details={"missing": missing, "checksum_mismatch": mismatched},
        )
    return manifest


@dataclass(frozen=True)
class RestoreResult:
    """What a restore honestly did."""

    backup_dir: Path
    replaced_paths: list[Path] = field(default_factory=list)
    blobs_restored: int = 0
    integrity: str = "unchecked"


def _move_aside(path: Path, stamp: str, moved: list[Path]) -> None:
    if path.exists():
        aside = _free_path(path.with_name(f"{path.name}.prerestore-{stamp}"))
        path.rename(aside)
        moved.append(aside)


def restore_backup(
    backup_dir: Path,
    ws: Workspace,
    *,
    force: bool = False,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
) -> RestoreResult:
    """Restore a verified backup into a workspace.

    Refuses to overwrite a live database without `force`; with `force` the
    displaced files are moved aside (`*.prerestore-<ts>`), never deleted —
    a failed restore never destroys the previous state.
    """
    clock = clock or SystemClock()
    manifest = verify_backup(backup_dir)
    ensure_workspace(ws)

    if ws.db_path.exists() and not force:
        raise ConflictError(
            f"workspace has live data: {ws.db_path} — pass force to restore "
            "over it (the current file is moved aside, never deleted)",
            details={"db_path": str(ws.db_path), "backup_dir": str(backup_dir)},
        )

    now = clock.now()
    stamp = _stamp(now)
    moved: list[Path] = []
    if ws.db_path.exists():
        for suffix in ("", "-wal", "-shm"):
            _move_aside(Path(str(ws.db_path) + suffix), stamp, moved)

    shutil.copyfile(backup_dir / "sentinel.db", ws.db_path)

    restored_blobs = 0
    blobs_in = backup_dir / "blobs"
    if blobs_in.is_dir():
        ws.blobs_dir.mkdir(exist_ok=True)
        for blob in sorted(blobs_in.iterdir()):
            if not blob.is_file():
                continue
            target = ws.blobs_dir / blob.name
            if target.exists():
                # content-addressed: same name must be same content
                if _sha256(target) != _sha256(blob):
                    raise StorageError(
                        "blob content conflict on restore — refusing to "
                        f"overwrite differing content: {blob.name}",
                        details={"blob": blob.name},
                    )
                continue  # identical content already present
            shutil.copyfile(blob, target)
            restored_blobs += 1

    if (backup_dir / "config.yaml").is_file() and not ws.config_path.exists():
        shutil.copyfile(backup_dir / "config.yaml", ws.config_path)

    conn = connect(ws.db_path)
    try:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        conn.close()
    if integrity != "ok" or fk_violations:
        raise StorageError(
            "restored database failed post-restore checks",
            details={
                "integrity": integrity,
                "foreign_key_violations": len(fk_violations),
            },
        )

    _journal(
        ws,
        clock,
        ids,
        action="operations.restore",
        details={
            "backup_dir": str(backup_dir),
            "backup_at": str(manifest.get("at")),
            "blobs_restored": str(restored_blobs),
            "replaced": str(len(moved)),
        },
    )
    return RestoreResult(
        backup_dir=backup_dir,
        replaced_paths=moved,
        blobs_restored=restored_blobs,
        integrity=integrity,
    )
