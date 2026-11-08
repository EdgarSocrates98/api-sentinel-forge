"""SQLite repository implementations + UnitOfWork."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from api_sentinel.application.ports import (
    AuditRepository,
    BatchRepository,
    CheckpointRepository,
    EnvironmentRepository,
    EvidenceRepository,
    ObservationRepository,
    QuarantineRepository,
    ReleaseRepository,
    ServiceRepository,
    SloAssessmentRepository,
    SloDefinitionRepository,
    SourceRepository,
)
from api_sentinel.domain import (
    ApiService,
    AuditEvent,
    EvidenceArtifact,
    ExternalRef,
    IngestionCheckpoint,
    ObservationBatch,
    Provenance,
    QualityState,
    ServiceEnvironment,
    ServiceRelease,
    SignalType,
    StorageError,
    TelemetrySource,
)
from api_sentinel.infrastructure.storage.db import connect
from api_sentinel.infrastructure.storage.migrations import migrate
from api_sentinel.ingestion.models import NormalizedMeasurement, NormalizedObservation
from api_sentinel.ingestion.quarantine import QuarantineEntry
from api_sentinel.reliability.slo import SloAssessment, SloDefinition


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class _ServiceRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> ApiService:
        return ApiService(
            service_id=r["service_id"],
            service_name=r["service_name"],
            environment_id=r["environment_id"],
            namespace=r["namespace"],
            display_name=r["display_name"],
            external_refs=[ExternalRef.model_validate(x) for x in json.loads(r["external_refs"])],
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    def get(self, service_id: str) -> ApiService | None:
        r = self._c.execute(
            "SELECT * FROM api_services WHERE service_id=?", (service_id,)
        ).fetchone()
        return self._row(r) if r else None

    def get_by_name(self, environment_id: str, service_name: str) -> ApiService | None:
        r = self._c.execute(
            "SELECT * FROM api_services WHERE environment_id=? AND service_name=?",
            (environment_id, service_name),
        ).fetchone()
        return self._row(r) if r else None

    def list(self) -> list[ApiService]:
        rows = self._c.execute("SELECT * FROM api_services ORDER BY service_name")
        return [self._row(r) for r in rows]

    def put(self, s: ApiService) -> ApiService:
        self._c.execute(
            "INSERT OR REPLACE INTO api_services"
            " (service_id, service_name, environment_id, namespace, display_name, external_refs,"
            "  created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                s.service_id, s.service_name, s.environment_id, s.namespace, s.display_name,
                json.dumps([x.model_dump() for x in s.external_refs]),
                s.created_at.isoformat(), s.updated_at.isoformat(),
            ),
        )
        return s


class _EnvironmentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def get_by_name(self, name: str) -> ServiceEnvironment | None:
        r = self._c.execute(
            "SELECT * FROM service_environments WHERE name=?", (name,)
        ).fetchone()
        if not r:
            return None
        return ServiceEnvironment(
            environment_id=r["environment_id"], name=r["name"], scope=r["scope"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def put(self, e: ServiceEnvironment) -> ServiceEnvironment:
        self._c.execute(
            "INSERT OR IGNORE INTO service_environments (environment_id, name, scope, created_at)"
            " VALUES (?,?,?,?)",
            (e.environment_id, e.name, e.scope, e.created_at.isoformat()),
        )
        return e


class _SourceRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> TelemetrySource:
        return TelemetrySource(
            source_id=r["source_id"], kind=r["kind"], service_id=r["service_id"],
            enabled=bool(r["enabled"]), config=json.loads(r["config"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def get(self, source_id: str) -> TelemetrySource | None:
        r = self._c.execute(
            "SELECT * FROM telemetry_sources WHERE source_id=?", (source_id,)
        ).fetchone()
        return self._row(r) if r else None

    def list(self, service_id: str | None = None) -> list[TelemetrySource]:
        if service_id:
            rows = self._c.execute(
                "SELECT * FROM telemetry_sources WHERE service_id=? ORDER BY source_id",
                (service_id,),
            )
        else:
            rows = self._c.execute("SELECT * FROM telemetry_sources ORDER BY source_id")
        return [self._row(r) for r in rows]

    def put(self, s: TelemetrySource) -> TelemetrySource:
        self._c.execute(
            "INSERT OR REPLACE INTO telemetry_sources"
            " (source_id, kind, service_id, enabled, config, created_at) VALUES (?,?,?,?,?,?)",
            (s.source_id, s.kind, s.service_id, int(s.enabled), json.dumps(s.config),
             s.created_at.isoformat()),
        )
        return s


class _ReleaseRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def put(self, r: ServiceRelease) -> ServiceRelease:
        self._c.execute(
            "INSERT OR REPLACE INTO service_releases"
            " (release_id, service_id, version, deployed_at, source, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (r.release_id, r.service_id, r.version,
             r.deployed_at.isoformat() if r.deployed_at else None, r.source,
             r.created_at.isoformat()),
        )
        return r


class _BatchRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def get_by_idempotency_key(self, key: str) -> ObservationBatch | None:
        r = self._c.execute(
            "SELECT * FROM observation_batches WHERE idempotency_key=?", (key,)
        ).fetchone()
        return self._row(r) if r else None

    def _row(self, r: sqlite3.Row) -> ObservationBatch:
        return ObservationBatch(
            batch_id=r["batch_id"],
            provenance=Provenance(
                source_id=r["source_id"],
                observed_at=datetime.fromisoformat(r["observed_at"]),
                received_at=datetime.fromisoformat(r["received_at"]),
                adapter_version=r["adapter_version"],
                transform_notes=r["transform_notes"],
            ),
            signal_types=[SignalType(x) for x in json.loads(r["signal_types"])],
            quality=QualityState(r["quality"]),
            item_count=r["item_count"],
            idempotency_key=r["idempotency_key"],
        )

    def put(self, b: ObservationBatch) -> ObservationBatch:
        self._c.execute(
            "INSERT OR IGNORE INTO observation_batches"
            " (batch_id, source_id, observed_at, received_at, signal_types, quality, item_count,"
            "  idempotency_key, adapter_version, transform_notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (b.batch_id, b.provenance.source_id, b.provenance.observed_at.isoformat(),
             b.provenance.received_at.isoformat(),
             json.dumps([s.value for s in b.signal_types]), b.quality.value, b.item_count,
             b.idempotency_key, b.provenance.adapter_version, b.provenance.transform_notes),
        )
        return b


class _EvidenceRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def get(self, evidence_id: str) -> EvidenceArtifact | None:
        r = self._c.execute(
            "SELECT * FROM evidence_artifacts WHERE evidence_id=?", (evidence_id,)
        ).fetchone()
        if not r:
            return None
        return EvidenceArtifact(
            evidence_id=r["evidence_id"], kind=r["kind"],
            created_at=datetime.fromisoformat(r["created_at"]), hash=r["hash"],
            size_bytes=r["size_bytes"], truncated=bool(r["truncated"]),
            loss_notes=r["loss_notes"], source_id=r["source_id"], blob_ref=r["blob_ref"],
        )

    def put(self, a: EvidenceArtifact) -> EvidenceArtifact:
        self._c.execute(
            "INSERT OR REPLACE INTO evidence_artifacts"
            " (evidence_id, kind, created_at, hash, size_bytes, truncated, loss_notes, source_id,"
            "  blob_ref) VALUES (?,?,?,?,?,?,?,?,?)",
            (a.evidence_id, a.kind, a.created_at.isoformat(), a.hash, a.size_bytes,
             int(a.truncated), a.loss_notes, a.source_id, a.blob_ref),
        )
        return a


class _AuditRepo:
    """Append-only by design: exposes no update/delete paths."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def append(self, e: AuditEvent) -> None:
        self._c.execute(
            "INSERT INTO audit_events"
            " (audit_id, at, actor, action, target_type, target_id, details)"
            " VALUES (?,?,?,?,?,?,?)",
            (e.audit_id, e.at.isoformat(), e.actor, e.action, e.target_type, e.target_id,
             json.dumps(e.details)),
        )

    def list(self, target_id: str | None = None) -> list[AuditEvent]:
        sql = "SELECT * FROM audit_events"
        args: tuple[Any, ...] = ()
        if target_id:
            sql += " WHERE target_id=?"
            args = (target_id,)
        rows = self._c.execute(sql + " ORDER BY at, audit_id", args).fetchall()
        return [
            AuditEvent(
                audit_id=r["audit_id"], at=datetime.fromisoformat(r["at"]), actor=r["actor"],
                action=r["action"], target_type=r["target_type"], target_id=r["target_id"],
                details=json.loads(r["details"]),
            )
            for r in rows
        ]


class _CheckpointRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def get(self, source_id: str) -> IngestionCheckpoint | None:
        r = self._c.execute(
            "SELECT * FROM ingestion_checkpoints WHERE source_id=?", (source_id,)
        ).fetchone()
        if not r:
            return None
        return IngestionCheckpoint(
            source_id=r["source_id"], checkpoint=r["checkpoint"],
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    def put(self, cp: IngestionCheckpoint) -> None:
        self._c.execute(
            "INSERT INTO ingestion_checkpoints (source_id, checkpoint, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(source_id) DO UPDATE SET checkpoint=excluded.checkpoint,"
            " updated_at=excluded.updated_at",
            (cp.source_id, cp.checkpoint, cp.updated_at.isoformat()),
        )


class _ObservationRepo:
    """Append-only observation store — INSERT OR IGNORE, never UPDATE."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> NormalizedObservation:
        return NormalizedObservation(
            observation_id=r["observation_id"],
            service_id=r["service_id"],
            operation_id=r["operation_id"],
            release_id=r["release_id"],
            window_start=datetime.fromisoformat(r["window_start"]),
            window_end=datetime.fromisoformat(r["window_end"]),
            ingest_time=datetime.fromisoformat(r["ingest_time"]),
            processing_time=datetime.fromisoformat(r["processing_time"]),
            source_id=r["source_id"],
            batch_id=r["batch_id"],
            quality=QualityState(r["quality"]),
            measurement=NormalizedMeasurement.model_validate(json.loads(r["measurement"])),
            attributes=json.loads(r["attributes"]),
            idempotency_key=r["idempotency_key"],
        )

    def get(self, observation_id: str) -> NormalizedObservation | None:
        r = self._c.execute(
            "SELECT * FROM runtime_observations WHERE observation_id=?", (observation_id,)
        ).fetchone()
        return self._row(r) if r else None

    def put(self, o: NormalizedObservation) -> None:
        self._c.execute(
            "INSERT OR IGNORE INTO runtime_observations"
            " (observation_id, service_id, operation_id, release_id, window_start,"
            "  window_end, ingest_time, processing_time, source_id, batch_id, quality,"
            "  measurement, attributes, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                o.observation_id, o.service_id, o.operation_id, o.release_id,
                o.window_start.isoformat(), o.window_end.isoformat(),
                o.ingest_time.isoformat(), o.processing_time.isoformat(),
                o.source_id, o.batch_id, o.quality.value,
                o.measurement.model_dump_json(), json.dumps(o.attributes, default=str),
                o.idempotency_key,
            ),
        )

    def list_by_batch(self, batch_id: str) -> list[NormalizedObservation]:
        rows = self._c.execute(
            "SELECT * FROM runtime_observations WHERE batch_id=? ORDER BY observation_id",
            (batch_id,),
        )
        return [self._row(r) for r in rows]

    def count(self) -> int:
        return int(self._c.execute("SELECT COUNT(*) FROM runtime_observations").fetchone()[0])

    def aggregates(self) -> dict[str, Any]:
        """Per-(service, source, quality, kind) counts — sources never summed."""
        rows = self._c.execute(
            "SELECT service_id, source_id, quality,"
            "       json_extract(measurement, '$.kind') AS kind, COUNT(*) AS n"
            " FROM runtime_observations"
            " GROUP BY service_id, source_id, quality, kind"
            " ORDER BY service_id, source_id, quality, kind"
        ).fetchall()
        return {
            "total": self.count(),
            "by": [
                {
                    "service_id": r["service_id"],
                    "source_id": r["source_id"],
                    "quality": r["quality"],
                    "kind": r["kind"],
                    "observations": r["n"],
                }
                for r in rows
            ],
        }

    def overlapping_sources(
        self,
        service_id: str,
        window_start: datetime,
        window_end: datetime,
        exclude_source_id: str,
    ) -> set[str]:
        rows = self._c.execute(
            "SELECT DISTINCT source_id FROM runtime_observations"
            " WHERE service_id=? AND source_id!=?"
            "   AND window_start < ? AND window_end > ?",
            (
                service_id,
                exclude_source_id,
                window_end.isoformat(),
                window_start.isoformat(),
            ),
        ).fetchall()
        return {r["source_id"] for r in rows}

    def raw_attributes(self, observation_id: str) -> str:
        r = self._c.execute(
            "SELECT attributes FROM runtime_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        return str(r["attributes"]) if r else ""

    def list_for_service(
        self,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[NormalizedObservation]:
        sql = "SELECT * FROM runtime_observations WHERE service_id=?"
        args: list[Any] = [service_id]
        if window_start is not None:
            sql += " AND window_end > ?"
            args.append(window_start.isoformat())
        if window_end is not None:
            sql += " AND window_start < ?"
            args.append(window_end.isoformat())
        sql += " ORDER BY window_start, observation_id"
        rows = self._c.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]


class _QuarantineRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> QuarantineEntry:
        return QuarantineEntry(
            item_id=r["item_id"],
            batch_id=r["batch_id"],
            reason=r["reason"],
            payload=r["payload"] or "",
            payload_truncated=bool(r["payload_truncated"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def put(self, e: QuarantineEntry) -> None:
        self._c.execute(
            "INSERT OR IGNORE INTO ingestion_quarantine"
            " (item_id, batch_id, reason, payload, payload_truncated, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                e.item_id, e.batch_id, e.reason, e.payload,
                int(e.payload_truncated), e.created_at.isoformat(),
            ),
        )

    def list(self, batch_id: str | None = None) -> list[QuarantineEntry]:
        sql = "SELECT * FROM ingestion_quarantine"
        args: tuple[Any, ...] = ()
        if batch_id:
            sql += " WHERE batch_id=?"
            args = (batch_id,)
        rows = self._c.execute(sql + " ORDER BY created_at, item_id", args)
        return [self._row(r) for r in rows]

    def count(self) -> int:
        return int(self._c.execute("SELECT COUNT(*) FROM ingestion_quarantine").fetchone()[0])


class _SloDefinitionRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> SloDefinition:
        return SloDefinition.model_validate(json.loads(r["definition"]))

    def get(self, slo_id: str, revision: int | None = None) -> SloDefinition | None:
        if revision is None:
            r = self._c.execute(
                "SELECT * FROM slo_definitions WHERE slo_id=?"
                " ORDER BY revision DESC LIMIT 1",
                (slo_id,),
            ).fetchone()
        else:
            r = self._c.execute(
                "SELECT * FROM slo_definitions WHERE slo_id=? AND revision=?",
                (slo_id, revision),
            ).fetchone()
        return self._row(r) if r else None

    def list(self, service_id: str | None = None) -> list[SloDefinition]:
        if service_id:
            rows = self._c.execute(
                "SELECT * FROM slo_definitions WHERE service_id=?"
                " ORDER BY slo_id, revision",
                (service_id,),
            )
        else:
            rows = self._c.execute(
                "SELECT * FROM slo_definitions ORDER BY slo_id, revision"
            )
        return [self._row(r) for r in rows]

    def put(self, definition: SloDefinition) -> None:
        # INSERT only: a revision is immutable; conflicts are handled by the
        # use case (identical → idempotent return, different → ConflictError).
        self._c.execute(
            "INSERT INTO slo_definitions (slo_id, revision, service_id, definition,"
            " created_at) VALUES (?,?,?,?,?)",
            (
                definition.slo_id,
                definition.revision,
                definition.service_id,
                definition.model_dump_json(),
                definition.created_at.isoformat(),
            ),
        )


class _SloAssessmentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> SloAssessment:
        return SloAssessment.model_validate(json.loads(r["assessment"]))

    def put(self, a: SloAssessment) -> None:
        # Re-evaluation of the same (slo_id, revision, window) slot replaces
        # the row — idempotent re-runs, never duplicates.
        self._c.execute(
            "INSERT INTO slo_assessments"
            " (assessment_id, slo_id, revision, window_start, window_end, sli,"
            "  eligible, good, budget_consumed, state, assessment, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(slo_id, revision, window_start, window_end)"
            " DO UPDATE SET assessment_id=excluded.assessment_id,"
            "   sli=excluded.sli, eligible=excluded.eligible, good=excluded.good,"
            "   budget_consumed=excluded.budget_consumed, state=excluded.state,"
            "   assessment=excluded.assessment, created_at=excluded.created_at",
            (
                a.assessment_id, a.slo_id, a.revision,
                a.window_start.isoformat(), a.window_end.isoformat(),
                a.sli, a.eligible, a.good, a.budget_consumed, a.state.value,
                a.model_dump_json(), a.evaluated_at.isoformat(),
            ),
        )

    def get(self, assessment_id: str) -> SloAssessment | None:
        r = self._c.execute(
            "SELECT * FROM slo_assessments WHERE assessment_id=?", (assessment_id,)
        ).fetchone()
        return self._row(r) if r else None

    def list(self, slo_id: str, revision: int | None = None) -> list[SloAssessment]:
        sql = "SELECT * FROM slo_assessments WHERE slo_id=?"
        args: list[Any] = [slo_id]
        if revision is not None:
            sql += " AND revision=?"
            args.append(revision)
        sql += " ORDER BY window_start, assessment_id"
        rows = self._c.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]


class SqliteUnitOfWork:
    """One connection, explicit commit/rollback; repos share the transaction."""

    services: ServiceRepository
    environments: EnvironmentRepository
    sources: SourceRepository
    releases: ReleaseRepository
    batches: BatchRepository
    evidence: EvidenceRepository
    audit: AuditRepository
    checkpoints: CheckpointRepository
    observations: ObservationRepository
    quarantine: QuarantineRepository
    slo_definitions: SloDefinitionRepository
    slo_assessments: SloAssessmentRepository

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _open(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = connect(self._db_path)
            migrate(self._conn)
        return self._conn

    def __enter__(self) -> SqliteUnitOfWork:
        conn = self._open()
        self.services = _ServiceRepo(conn)
        self.environments = _EnvironmentRepo(conn)
        self.sources = _SourceRepo(conn)
        self.releases = _ReleaseRepo(conn)
        self.batches = _BatchRepo(conn)
        self.evidence = _EvidenceRepo(conn)
        self.audit = _AuditRepo(conn)
        self.checkpoints = _CheckpointRepo(conn)
        self.observations = _ObservationRepo(conn)
        self.quarantine = _QuarantineRepo(conn)
        self.slo_definitions = _SloDefinitionRepo(conn)
        self.slo_assessments = _SloAssessmentRepo(conn)
        conn.execute("BEGIN")
        return self

    def commit(self) -> None:
        if self._conn is None:
            raise StorageError("unit of work not open")
        if self._conn.in_transaction:
            self._conn.execute("COMMIT")

    def rollback(self) -> None:
        if self._conn is None:
            raise StorageError("unit of work not open")
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK")

    def __exit__(self, exc_type: object, _exc_val: object, _exc_tb: object) -> None:
        if self._conn is None:
            return
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self._conn.close()
            self._conn = None
