"""Retention enforcement — docs/10 policy proven, evidence-protection first.

The hard rule (docs/10, ADR-015): automatic deletion never destroys evidence
referenced by live investigations/incidents/anomalies. This suite seeds a
workspace with every retention class and proves:

- dry-run counts candidates and deletes nothing;
- `--apply` purges aged observations/quarantine/terminal incidents;
- evidence referenced by an *active* incident or an anomaly is protected —
  even when older than the evidence policy;
- evidence belonging to a *purged* incident loses protection and dies with
  its incident (row + blob);
- orphan evidence rows older than policy are removed, blob included;
- unreferenced blob *files* are reported as orphans, never silently dropped;
- receipts and the audit row are journaled.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from api_sentinel.detection import (
    Anomaly,
    AnomalyStatus,
    Explanation,
    RuleRef,
    Severity,
)
from api_sentinel.domain import (
    ApiService,
    EvidenceArtifact,
    FixedClock,
    IngestionCheckpoint,
    MeasurementKind,
    ObservationBatch,
    Provenance,
    QualityState,
    SequentialIdFactory,
    ServiceEnvironment,
    TelemetrySource,
)
from api_sentinel.incidents import Incident, IncidentStatus
from api_sentinel.infrastructure.storage import FileBlobStore, SqliteUnitOfWork
from api_sentinel.ingestion.models import (
    NormalizedMeasurement,
    NormalizedObservation,
)
from api_sentinel.ingestion.quarantine import QuarantineEntry
from api_sentinel.operations.retention import RetentionPolicy, run_retention
from api_sentinel.workspace import Workspace, ensure_workspace

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
SERVICE = "svc_ret"
SOURCE = "src_ret"


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _obs(
    obs_id: str,
    *,
    batch_id: str,
    w_start: datetime,
    w_end: datetime,
) -> NormalizedObservation:
    return NormalizedObservation(
        observation_id=obs_id,
        service_id=SERVICE,
        operation_id=None,
        release_id=None,
        window_start=w_start,
        window_end=w_end,
        ingest_time=w_end,
        processing_time=w_end,
        source_id=SOURCE,
        batch_id=batch_id,
        quality=QualityState.COMPLETE,
        measurement=NormalizedMeasurement(kind=MeasurementKind.COUNTER, unit="requests", value=1.0),
        attributes={},
        idempotency_key=f"key_{obs_id}",
    )


def _incident(
    incident_id: str,
    *,
    status: IncidentStatus,
    detected: datetime,
    resolved: datetime | None,
    evidence: list[str],
) -> Incident:
    return Incident(
        incident_id=incident_id,
        title=f"{incident_id} title",
        severity=Severity.SEV3,
        status=status,
        service_id=SERVICE,
        group_key=f"{SERVICE}|service|{incident_id}",
        first_observed_at=detected,
        detected_at=detected,
        resolved_at=resolved,
        evidence_refs=evidence,
    )


def _seed(ws_root: Path) -> Workspace:
    ws = ensure_workspace(Workspace(root=ws_root, scope="project"))
    blobs = FileBlobStore(ws.blobs_dir)
    with SqliteUnitOfWork(ws.db_path) as uow:
        env = uow.environments.put(
            ServiceEnvironment(environment_id="env_ret", name="lab", created_at=_ago(400))
        )
        uow.services.put(
            ApiService(
                service_id=SERVICE,
                service_name="ret-api",
                environment_id=env.environment_id,
                created_at=_ago(400),
                updated_at=_ago(400),
            )
        )
        uow.sources.put(
            TelemetrySource(
                source_id=SOURCE,
                kind="jsonl-file",
                service_id=SERVICE,
                created_at=_ago(400),
            )
        )
        uow.batches.put(
            ObservationBatch(
                batch_id="batch_ret",
                provenance=Provenance(
                    source_id=SOURCE,
                    observed_at=_ago(400),
                    received_at=_ago(400),
                ),
                item_count=8,
            )
        )
        uow.checkpoints.put(IngestionCheckpoint(source_id=SOURCE, checkpoint="8", updated_at=NOW))

        # --- observations: three width classes at ages around the tiers ---
        uow.observations.put(
            _obs(
                "obs_detailed_old",
                batch_id="batch_ret",
                w_start=_ago(10),
                w_end=_ago(10) + timedelta(seconds=30),
            )
        )  # detailed, 10d old > 7d → purge
        uow.observations.put(
            _obs(
                "obs_detailed_new",
                batch_id="batch_ret",
                w_start=_ago(1),
                w_end=_ago(1) + timedelta(seconds=30),
            )
        )  # detailed, 1d old → keep
        uow.observations.put(
            _obs(
                "obs_minute_old",
                batch_id="batch_ret",
                w_start=_ago(40),
                w_end=_ago(40) + timedelta(seconds=60),
            )
        )  # minute class, 40d > 30d → purge
        uow.observations.put(
            _obs(
                "obs_minute_new",
                batch_id="batch_ret",
                w_start=_ago(20),
                w_end=_ago(20) + timedelta(seconds=60),
            )
        )  # minute class, 20d → keep
        uow.observations.put(
            _obs(
                "obs_hourly_old",
                batch_id="batch_ret",
                w_start=_ago(100),
                w_end=_ago(100) + timedelta(seconds=3600),
            )
        )  # hourly class, 100d > 90d → purge
        uow.observations.put(
            _obs(
                "obs_hourly_new",
                batch_id="batch_ret",
                w_start=_ago(50),
                w_end=_ago(50) + timedelta(seconds=3600),
            )
        )  # hourly class, 50d → keep

        # --- quarantine: one old entry → purge ---
        uow.quarantine.put(
            QuarantineEntry(
                item_id="q_old",
                batch_id="batch_ret",
                reason="schema_violation",
                payload="{}",
                created_at=_ago(40),
            )
        )

        # --- evidence ---
        def ev(evidence_id: str, *, created: datetime) -> EvidenceArtifact:
            blob_ref = blobs.put(f"payload {evidence_id}".encode())
            return EvidenceArtifact(
                evidence_id=evidence_id,
                kind="metric-export",
                created_at=created,
                hash=blob_ref,
                size_bytes=len(f"payload {evidence_id}"),
                source_id=SOURCE,
                blob_ref=blob_ref,
            )

        # old terminal incident referencing its own evidence → both die
        uow.evidence.put(ev("ev_dead_inc", created=_ago(200)))
        uow.incidents.put(
            _incident(
                "inc_dead",
                status=IncidentStatus.CLOSED,
                detected=_ago(200),
                resolved=_ago(195),
                evidence=["ev_dead_inc"],
            )
        )
        # ACTIVE incident (old) referencing old evidence → protected
        uow.evidence.put(ev("ev_active", created=_ago(200)))
        uow.incidents.put(
            _incident(
                "inc_active",
                status=IncidentStatus.INVESTIGATING,
                detected=_ago(200),
                resolved=None,
                evidence=["ev_active"],
            )
        )
        # anomaly-referenced evidence → protected even without an incident
        uow.evidence.put(ev("ev_anom", created=_ago(200)))
        uow.anomalies.put(
            Anomaly(
                anomaly_id="anm_ret",
                service_id=SERVICE,
                operation_id=None,
                detected_at=_ago(200),
                window_start=_ago(200),
                window_end=_ago(200) + timedelta(minutes=1),
                rule=RuleRef(
                    rule_id="rule_ret",
                    rule_revision=1,
                    detector_id="availability",
                    detector_version="1",
                ),
                severity=Severity.SEV3,
                status=AnomalyStatus.OBSERVED,
                explanation=Explanation(summary="retention lab anomaly"),
                evidence_refs=["ev_anom"],
                dedup_key="service",
            )
        )
        # orphan evidence: referenced by nothing, older than policy → dies
        uow.evidence.put(ev("ev_orphan", created=_ago(200)))
        # recent orphan: referenced by nothing but young → survives
        uow.evidence.put(ev("ev_orphan_young", created=_ago(1)))
        uow.commit()
    # blob file with no evidence row at all → reported orphan, kept
    (ws.blobs_dir / ("deadbeef" * 8)).write_bytes(b"stray blob")
    return ws


def _count(db: Path, table: str, where: str = "") -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(
            conn.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0]  # noqa: S608
        )
    finally:
        conn.close()


class TestRetentionDryRun:
    def test_dry_run_counts_and_deletes_nothing(self, tmp_path: Path) -> None:
        ws = _seed(tmp_path / "ws")
        report = run_retention(
            ws,
            apply=False,
            policy=RetentionPolicy(),
            clock=FixedClock(NOW),
            ids=SequentialIdFactory(),
        )
        assert report.candidates["observations"] == 3
        assert report.candidates["quarantine"] == 1
        assert report.candidates["incidents"] == 1
        assert report.candidates["evidence"] == 2  # dead-incident + orphan
        assert report.deleted == {}
        # nothing actually deleted
        assert _count(ws.db_path, "runtime_observations") == 6
        assert _count(ws.db_path, "incidents") == 2
        assert _count(ws.db_path, "evidence_artifacts") == 5
        assert report.receipt is None


class TestRetentionApply:
    def test_apply_enforces_policy_and_protects_evidence(self, tmp_path: Path) -> None:
        ws = _seed(tmp_path / "ws")
        ids = SequentialIdFactory()
        report = run_retention(
            ws,
            apply=True,
            policy=RetentionPolicy(),
            clock=FixedClock(NOW),
            ids=ids,
            receipts_dir=ws.receipts_dir,
        )
        assert report.deleted["observations"] == 3
        assert report.deleted["quarantine"] == 1
        assert report.deleted["incidents"] == 1
        assert report.deleted["evidence"] == 2
        assert report.deleted["evidence_blobs"] == 2

        conn = sqlite3.connect(str(ws.db_path))
        try:
            obs_ids = {
                r[0] for r in conn.execute("SELECT observation_id FROM runtime_observations")
            }
            assert obs_ids == {
                "obs_detailed_new",
                "obs_minute_new",
                "obs_hourly_new",
            }
            incident_ids = {r[0] for r in conn.execute("SELECT incident_id FROM incidents")}
            assert incident_ids == {"inc_active"}
            evidence_ids = {
                r[0] for r in conn.execute("SELECT evidence_id FROM evidence_artifacts")
            }
            # active-incident + anomaly evidence protected; young orphan kept
            assert evidence_ids == {"ev_active", "ev_anom", "ev_orphan_young"}
            # anomaly survived but its incident link semantics unchanged
            assert _count(ws.db_path, "anomalies") == 1
            quarantine = _count(ws.db_path, "ingestion_quarantine")
            assert quarantine == 0
            # operations journaled an audit row (append-only)
            actions = [
                r[0]
                for r in conn.execute(
                    "SELECT action FROM audit_events WHERE action='operations.retention'"
                )
            ]
            assert len(actions) == 1
        finally:
            conn.close()

        # blobs: protected/youth evidence files stay; purged blobs gone
        blob_names = {p.name for p in ws.blobs_dir.iterdir() if p.is_file()}
        for ev_id in ("ev_active", "ev_anom", "ev_orphan_young"):
            with SqliteUnitOfWork(ws.db_path) as uow:
                artifact = uow.evidence.get(ev_id)
                assert artifact is not None and artifact.blob_ref is not None
            assert artifact.blob_ref.removeprefix("sha256:") in blob_names
        assert ("deadbeef" * 8) in blob_names  # stray file reported, kept
        assert report.orphan_blob_files == 1

        # receipt exists and records the applied report
        assert report.receipt is not None
        receipt = json.loads(Path(report.receipt).read_text("utf-8"))
        assert receipt["apply"] is True
        assert receipt["deleted"]["evidence"] == 2

    def test_reapply_is_idempotent(self, tmp_path: Path) -> None:
        ws = _seed(tmp_path / "ws")
        ids = SequentialIdFactory()
        first = run_retention(
            ws,
            apply=True,
            policy=RetentionPolicy(),
            clock=FixedClock(NOW),
            ids=ids,
        )
        second = run_retention(
            ws,
            apply=True,
            policy=RetentionPolicy(),
            clock=FixedClock(NOW),
            ids=ids,
        )
        assert second.deleted["observations"] == 0
        assert second.deleted["incidents"] == 0
        assert second.deleted["evidence"] == 0
        assert _count(ws.db_path, "runtime_observations") == 3
        assert first.deleted["observations"] == 3

    def test_open_incident_never_purged_regardless_of_age(self, tmp_path: Path) -> None:
        ws = _seed(tmp_path / "ws")
        run_retention(
            ws,
            apply=True,
            policy=RetentionPolicy(incidents_days=1),
            clock=FixedClock(NOW),
            ids=SequentialIdFactory(),
        )
        assert _count(ws.db_path, "incidents") == 1  # inc_active survives
        with SqliteUnitOfWork(ws.db_path) as uow:
            assert uow.evidence.get("ev_active") is not None
