"""Sentinel self-observability — the Sentinel's own operational indicators.

Computed live from the workspace store at read time — nothing is cached into
a second store, so a stale metric is never mistaken for fresh signal
(docs/13: "a missing source never yields fabricated positive health" applies
to the Sentinel itself). Indicators carry `sentinel.*` names; they surface at
`api-sentinel doctor` and `GET /v1/health` (additive field), and operations
journaled by this package (backup/restore/retention) are the audit rows.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from api_sentinel.infrastructure.storage.db import connect
from api_sentinel.workspace import Workspace

STALE_AFTER_SECONDS = 300


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608


def _minmax(
    conn: sqlite3.Connection, table: str, column: str
) -> tuple[str | None, str | None]:
    row = conn.execute(
        f"SELECT MIN({column}) AS lo, MAX({column}) AS hi FROM {table}"  # noqa: S608
    ).fetchone()
    return (row["lo"], row["hi"]) if row else (None, None)


def _blob_stats(ws: Workspace, conn: sqlite3.Connection) -> dict[str, Any]:
    """Disk-side blob inventory + orphan detection (files unreferenced by
    `evidence_artifacts.blob_ref` — reported, never silently dropped, docs/15)."""
    referenced = {
        row["blob_ref"].removeprefix("sha256:")
        for row in conn.execute(
            "SELECT blob_ref FROM evidence_artifacts WHERE blob_ref IS NOT NULL"
        )
    }
    files = [p for p in ws.blobs_dir.iterdir() if p.is_file()] if ws.blobs_dir.is_dir() else []
    orphans = sorted(p.name for p in files if p.name not in referenced)
    return {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "orphans": len(orphans),
        "orphan_sample": orphans[:5],
    }


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {t: _count(conn, t) for t in tables}


def _source_freshness(
    conn: sqlite3.Connection, *, now: datetime, stale_after_seconds: int
) -> dict[str, Any]:
    """Per-source checkpoint freshness — a source that never ingested is
    `never_ingested`; one whose checkpoint is older than the staleness bound
    is `stale`. Both are named unknowns, never a fabricated green."""
    sources = conn.execute(
        "SELECT source_id, enabled FROM telemetry_sources ORDER BY source_id"
    ).fetchall()
    checkpoints = {
        r["source_id"]: r["updated_at"]
        for r in conn.execute(
            "SELECT source_id, updated_at FROM ingestion_checkpoints"
        )
    }
    per_source: list[dict[str, Any]] = []
    stale: list[str] = []
    never_ingested: list[str] = []
    oldest_checkpoint: str | None = None
    for src in sources:
        source_id = src["source_id"]
        updated_raw = checkpoints.get(source_id)
        entry: dict[str, Any] = {
            "source_id": source_id,
            "enabled": bool(src["enabled"]),
        }
        if updated_raw is None:
            entry["state"] = "never_ingested"
            if src["enabled"]:
                never_ingested.append(source_id)
        else:
            updated = datetime.fromisoformat(updated_raw)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            age = (now - updated).total_seconds()
            entry["checkpoint_updated_at"] = updated_raw
            entry["checkpoint_age_seconds"] = max(0.0, round(age, 3))
            if age > stale_after_seconds:
                entry["state"] = "stale"
                if src["enabled"]:
                    stale.append(source_id)
            else:
                entry["state"] = "fresh"
            if oldest_checkpoint is None or updated_raw < oldest_checkpoint:
                oldest_checkpoint = updated_raw
        per_source.append(entry)
    return {
        "count": len(sources),
        "stale": sorted(stale),
        "never_ingested": sorted(never_ingested),
        "oldest_checkpoint_updated_at": oldest_checkpoint,
        "per_source": per_source,
        "stale_after_seconds": stale_after_seconds,
    }


def sentinel_health(
    ws: Workspace,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = STALE_AFTER_SECONDS,
) -> dict[str, Any]:
    """The Sentinel's own state — computed live, honest about absences.

    Absent database → `state=unavailable` with `database_absent` unknown, not
    a zero-valued report: missing data stays missing.
    """
    at = now or datetime.now(UTC)
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    report: dict[str, Any] = {
        "at": at.isoformat(),
        "workspace_initialized": ws.root.is_dir(),
        "unknowns": [],
    }
    if not ws.db_path.is_file():
        report.update(
            {
                "state": "unavailable",
                "database": {"present": False},
            }
        )
        report["unknowns"] = ["database_absent"]
        return report

    conn = connect(ws.db_path)
    try:
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        observations_lo, observations_hi = _minmax(
            conn, "runtime_observations", "window_end"
        )
        quarantine_lo, _ = _minmax(conn, "ingestion_quarantine", "created_at")
        open_incidents = int(
            conn.execute(
                "SELECT COUNT(*) FROM incidents WHERE status IN"
                " ('DETECTED','TRIAGED','INVESTIGATING','MITIGATING',"
                "  'MONITORING','REOPENED')"
            ).fetchone()[0]
        )
        report.update(
            {
                "state": "available" if integrity == "ok" else "degraded",
                "database": {
                    "present": True,
                    "size_bytes": ws.db_path.stat().st_size,
                    "integrity": integrity,
                },
                "tables": _table_counts(conn),
                "observations": {
                    "count": _count(conn, "runtime_observations"),
                    "oldest_window_end": observations_lo,
                    "newest_window_end": observations_hi,
                },
                "quarantine": {
                    "depth": _count(conn, "ingestion_quarantine"),
                    "oldest_created_at": quarantine_lo,
                },
                "incidents": {
                    "open": open_incidents,
                    "total": _count(conn, "incidents"),
                },
                "audit_events": _count(conn, "audit_events"),
                "sources": _source_freshness(
                    conn, now=at, stale_after_seconds=stale_after_seconds
                ),
                "blobs": _blob_stats(ws, conn),
            }
        )
    finally:
        conn.close()

    unknowns: list[str] = []
    sources = report["sources"]
    if sources["count"] == 0:
        unknowns.append("no_sources_registered")
    unknowns.extend(f"source_stale:{s}" for s in sources["stale"])
    unknowns.extend(f"source_never_ingested:{s}" for s in sources["never_ingested"])
    if report["database"]["integrity"] != "ok":
        unknowns.append("database_integrity_check_failed")
    if report["blobs"]["orphans"]:
        unknowns.append(f"orphan_blobs:{report['blobs']['orphans']}")
    report["unknowns"] = sorted(unknowns)
    return report
