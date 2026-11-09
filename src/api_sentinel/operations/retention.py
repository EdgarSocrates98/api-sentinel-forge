"""Retention enforcement — docs/10 policy over the workspace store (ADR-015).

Tiers (defaults; configurable per workspace `config.yaml → retention:` and
per run via CLI overrides):

| data class | default | cutoff column |
| --- | --- | --- |
| detailed observations (window < 60s) | 7d | `window_end` |
| minute-class observations (60s ≤ w < 3600s) | 30d | `window_end` |
| hourly-class observations (w ≥ 3600s) | 90d | `window_end` |
| quarantine entries | 30d | `created_at` |
| incidents (terminal only) | 180d | last journal/resolved/detected `at` |
| evidence artifacts | incident policy (180d) | `created_at` |
| audit events | never (opt-in only) | `at` |

Hard rule (docs/10): deletion never destroys evidence referenced by live
state — the protected set is rebuilt from surviving incidents (row JSON,
hypotheses and verifications), anomalies, investigations and dependency
observations *before* any evidence row or blob is removed. An evidence row
whose referencing incident was purged stops being protected — "incident
evidence lives and dies with its incident" (ADR-015). Blob files with no
`evidence_artifacts` row at all are *reported* as orphans, never deleted
here (docs/15: orphan sweep is a report, not a silent drop).

Dry-run is the default: the same candidate computation runs with zero
deletes. `--apply` executes inside one transaction, then writes a journaled
receipt under `receipts/` and an `audit_events` row.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from api_sentinel.domain import (
    Clock,
    IdFactory,
    NotFoundError,
    SystemClock,
)
from api_sentinel.infrastructure.storage.db import connect
from api_sentinel.workspace import Workspace, load_config

MINUTE_SECONDS = 60
HOUR_SECONDS = 3600

TERMINAL_INCIDENT_STATUSES = ("RESOLVED", "DISMISSED", "CLOSED")


@dataclass(frozen=True)
class RetentionPolicy:
    """Per-class retention in days (docs/10 defaults, ADR-015 configurable)."""

    observations_detailed_days: int = 7
    observations_minute_days: int = 30
    observations_hourly_days: int = 90
    quarantine_days: int = 30
    incidents_days: int = 180
    evidence_days: int = 180  # incident evidence lives with its incident
    audit_days: int | None = None  # None → audit trail is never auto-purged

    @staticmethod
    def from_config(config: dict[str, Any]) -> RetentionPolicy:
        raw = config.get("retention")
        if not isinstance(raw, dict):
            return RetentionPolicy()
        known = set(RetentionPolicy.__dataclass_fields__)
        values = {k: v for k, v in raw.items() if k in known}
        return RetentionPolicy(**values)

    def with_overrides(self, overrides: dict[str, int | None]) -> RetentionPolicy:
        known = set(RetentionPolicy.__dataclass_fields__)
        clean = {k: v for k, v in overrides.items() if k in known and v is not None}
        return RetentionPolicy(**{**asdict(self), **clean})


def _cutoff(now: datetime, days: int) -> str:
    return (now - timedelta(days=days)).isoformat()


def _evidence_refs_in(node: Any, found: set[str]) -> None:
    """Collect evidence ids anywhere a JSON blob mentions them — string
    values under `evidence*` keys, including list forms."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("evidence"):
                if isinstance(value, str):
                    found.add(value)
                elif isinstance(value, list):
                    found.update(v for v in value if isinstance(v, str))
                else:
                    _evidence_refs_in(value, found)
            else:
                _evidence_refs_in(value, found)
    elif isinstance(node, list):
        for item in node:
            _evidence_refs_in(item, found)


def _protected_evidence(conn: Any, surviving_incidents: set[str]) -> set[str]:
    """Evidence ids referenced by live state — never deletable."""
    protected: set[str] = set()
    for incident_id in surviving_incidents:
        row = conn.execute(
            "SELECT state FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        if row:
            _evidence_refs_in(json.loads(row["state"]), protected)
    for row in conn.execute("SELECT evidence_refs FROM anomalies"):
        _evidence_refs_in(
            {"evidence_refs": json.loads(row["evidence_refs"])}, protected
        )
    for row in conn.execute(
        "SELECT context_pack, result FROM investigations"
    ):
        _evidence_refs_in(json.loads(row["context_pack"]), protected)
        _evidence_refs_in(json.loads(row["result"]), protected)
    for row in conn.execute("SELECT evidence_refs FROM dependency_observations"):
        _evidence_refs_in(
            {"evidence_refs": json.loads(row["evidence_refs"])}, protected
        )
    return protected


def _incident_last_activity(conn: Any, incident_id: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(at) AS last_at FROM incident_events WHERE incident_id=?",
        (incident_id,),
    ).fetchone()
    return row["last_at"] if row else None


def _observation_class(width_seconds: float) -> str:
    if width_seconds < MINUTE_SECONDS:
        return "detailed"
    if width_seconds < HOUR_SECONDS:
        return "minute"
    return "hourly"


@dataclass
class RetentionReport:
    """What the run saw and did — identical shape for dry-run and apply."""

    at: str
    apply: bool
    policy: dict[str, Any]
    candidates: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)
    protected: dict[str, int] = field(default_factory=dict)
    orphan_blob_files: int = 0
    receipt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_retention(
    ws: Workspace,
    *,
    apply: bool = False,
    policy: RetentionPolicy | None = None,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
    receipts_dir: Path | None = None,
) -> RetentionReport:
    """Evaluate (and optionally execute) the retention policy.

    `apply=False` computes the same candidate sets and deletes nothing.
    `apply=True` deletes inside one transaction, journals a receipt and an
    audit row — every deletion is countable and attributable.
    """
    clock = clock or SystemClock()
    now = clock.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if policy is None:
        try:
            policy = RetentionPolicy.from_config(load_config(ws))
        except Exception:
            policy = RetentionPolicy()
    if not ws.db_path.is_file():
        raise NotFoundError(f"database not found: {ws.db_path}")

    report = RetentionReport(
        at=now.isoformat(),
        apply=apply,
        policy=asdict(policy),
    )

    conn = connect(ws.db_path)
    try:
        conn.execute("BEGIN")
        # --- observations: three classes by measured window width ---
        obs_cutoffs = {
            "detailed": _cutoff(now, policy.observations_detailed_days),
            "minute": _cutoff(now, policy.observations_minute_days),
            "hourly": _cutoff(now, policy.observations_hourly_days),
        }
        obs_rows = conn.execute(
            "SELECT observation_id, window_start, window_end"
            " FROM runtime_observations"
        ).fetchall()
        obs_delete: list[str] = []
        obs_class_counts = {"detailed": 0, "minute": 0, "hourly": 0}
        for row in obs_rows:
            w_start = datetime.fromisoformat(row["window_start"])
            w_end = datetime.fromisoformat(row["window_end"])
            cls = _observation_class((w_end - w_start).total_seconds())
            if row["window_end"] < obs_cutoffs[cls]:
                obs_delete.append(row["observation_id"])
                obs_class_counts[cls] += 1

        # --- quarantine ---
        quarantine_rows = [
            r["item_id"]
            for r in conn.execute(
                "SELECT item_id, created_at FROM ingestion_quarantine"
                " WHERE created_at < ?",
                (_cutoff(now, policy.quarantine_days),),
            )
        ]

        # --- terminal incidents past policy ---
        incident_cutoff = _cutoff(now, policy.incidents_days)
        incident_rows = conn.execute(
            "SELECT incident_id, status, resolved_at, detected_at"
            " FROM incidents"
        ).fetchall()
        surviving_incidents: set[str] = set()
        purge_incidents: list[str] = []
        open_incidents = 0
        for row in incident_rows:
            if row["status"] not in TERMINAL_INCIDENT_STATUSES:
                open_incidents += 1
                surviving_incidents.add(row["incident_id"])
                continue
            last = _incident_last_activity(conn, row["incident_id"])
            marker = last or row["resolved_at"] or row["detected_at"]
            if marker < incident_cutoff:
                purge_incidents.append(row["incident_id"])
            else:
                surviving_incidents.add(row["incident_id"])

        # --- evidence: protected set first, purge unreferenced second ---
        protected = _protected_evidence(conn, surviving_incidents)
        evidence_cutoff = _cutoff(now, policy.evidence_days)
        evidence_rows = conn.execute(
            "SELECT evidence_id, blob_ref, created_at FROM evidence_artifacts"
        ).fetchall()
        evidence_delete: list[tuple[str, str | None]] = []
        for row in evidence_rows:
            if row["evidence_id"] in protected:
                continue
            if row["created_at"] < evidence_cutoff:
                evidence_delete.append((row["evidence_id"], row["blob_ref"]))

        # --- audit trail: opt-in only ---
        audit_delete = 0
        if policy.audit_days is not None:
            audit_delete = int(
                conn.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE at < ?",
                    (_cutoff(now, policy.audit_days),),
                ).fetchone()[0]
            )

        report.candidates = {
            "observations": len(obs_delete),
            "observations_detailed": obs_class_counts["detailed"],
            "observations_minute": obs_class_counts["minute"],
            "observations_hourly": obs_class_counts["hourly"],
            "quarantine": len(quarantine_rows),
            "incidents": len(purge_incidents),
            "evidence": len(evidence_delete),
            "audit_events": audit_delete,
        }
        report.protected = {
            "incidents_open": open_incidents,
            "incidents_kept": len(surviving_incidents),
            "evidence_referenced": len(protected),
        }

        if apply:
            for obs_id in obs_delete:
                conn.execute(
                    "DELETE FROM runtime_observations WHERE observation_id=?",
                    (obs_id,),
                )
            for item_id in quarantine_rows:
                conn.execute(
                    "DELETE FROM ingestion_quarantine WHERE item_id=?",
                    (item_id,),
                )
            for incident_id in purge_incidents:
                # journal dies with its incident; anomaly rows persist but
                # the attachment link clears (FK), anomaly content kept.
                conn.execute(
                    "DELETE FROM investigations WHERE incident_id=?",
                    (incident_id,),
                )
                conn.execute(
                    "DELETE FROM incident_events WHERE incident_id=?",
                    (incident_id,),
                )
                conn.execute(
                    "UPDATE anomalies SET incident_id=NULL WHERE incident_id=?",
                    (incident_id,),
                )
                conn.execute(
                    "DELETE FROM incidents WHERE incident_id=?",
                    (incident_id,),
                )
            blob_files_deleted = 0
            for evidence_id, blob_ref in evidence_delete:
                conn.execute(
                    "DELETE FROM evidence_artifacts WHERE evidence_id=?",
                    (evidence_id,),
                )
                if blob_ref and blob_ref.startswith("sha256:"):
                    blob = ws.blobs_dir / blob_ref.removeprefix("sha256:")
                    if blob.is_file():
                        blob.unlink()
                        blob_files_deleted += 1
            if policy.audit_days is not None:
                conn.execute(
                    "DELETE FROM audit_events WHERE at < ?",
                    (_cutoff(now, policy.audit_days),),
                )
            report.deleted = {
                "observations": len(obs_delete),
                "quarantine": len(quarantine_rows),
                "incidents": len(purge_incidents),
                "evidence": len(evidence_delete),
                "evidence_blobs": blob_files_deleted,
                "audit_events": audit_delete,
            }
            conn.execute("COMMIT")
        else:
            conn.execute("ROLLBACK")

        # orphan blob files: reported, never deleted here (docs/15)
        referenced_blobs = {
            r["blob_ref"].removeprefix("sha256:")
            for r in conn.execute(
                "SELECT blob_ref FROM evidence_artifacts"
                " WHERE blob_ref IS NOT NULL"
            )
        }
        if ws.blobs_dir.is_dir():
            report.orphan_blob_files = sum(
                1
                for p in ws.blobs_dir.iterdir()
                if p.is_file() and p.name not in referenced_blobs
            )
    finally:
        conn.close()

    if apply and ids is not None:
        out = receipts_dir or ws.receipts_dir
        out.mkdir(parents=True, exist_ok=True)
        stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        receipt = out / f"retention-{stamp}.json"
        receipt.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        report.receipt = str(receipt)
        conn = connect(ws.db_path)
        try:
            conn.execute(
                "INSERT INTO audit_events"
                " (audit_id, at, actor, action, target_type, target_id, details)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    ids.new("aud"),
                    now.isoformat(),
                    "operations",
                    "operations.retention",
                    "workspace",
                    str(ws.root),
                    json.dumps(
                        {k: str(v) for k, v in report.deleted.items()}
                        | {"receipt": str(receipt)}
                    ),
                ),
            )
        finally:
            conn.close()
    return report


__all__ = [
    "RetentionPolicy",
    "RetentionReport",
    "run_retention",
]
