"""SQLite repository implementations + UnitOfWork."""

from __future__ import annotations

import builtins
import json
import sqlite3
from bisect import bisect_left
from collections.abc import Collection
from datetime import datetime
from itertools import accumulate
from pathlib import Path
from typing import Any

from api_sentinel.application.jobs import (
    IngestionJob,
    IngestionJobStatus,
    Investigation,
    InvestigationStatus,
)
from api_sentinel.application.ports import (
    AnomalyRepository,
    AuditRepository,
    BatchRepository,
    ChangeEventRepository,
    CheckpointRepository,
    DependencyObservationRepository,
    DetectionRuleRepository,
    EnvironmentRepository,
    EvidenceRepository,
    HandoffRecordRepository,
    IncidentEventRepository,
    IncidentRepository,
    IngestionJobRepository,
    InvestigationRepository,
    ObservationRepository,
    OperationRepository,
    QuarantineRepository,
    ReleaseRepository,
    ServiceRepository,
    SloAssessmentRepository,
    SloDefinitionRepository,
    SourceRepository,
)
from api_sentinel.detection.protocol import Anomaly, AnomalyStatus
from api_sentinel.detection.registry import DetectionRule
from api_sentinel.domain import (
    ApiOperation,
    ApiService,
    AuditEvent,
    ChangeEvent,
    ConflictError,
    DependencyObservation,
    EvidenceArtifact,
    ExternalHandoff,
    ExternalRef,
    HandoffStatus,
    IngestionCheckpoint,
    ObservationBatch,
    OperationIdentity,
    Provenance,
    QualityState,
    ServiceEnvironment,
    ServiceRelease,
    SignalType,
    StorageError,
    TelemetrySource,
)
from api_sentinel.incidents.journal import IncidentEvent
from api_sentinel.incidents.models import Incident, IncidentStatus
from api_sentinel.infrastructure.storage.db import connect
from api_sentinel.infrastructure.storage.migrations import migrate
from api_sentinel.ingestion.models import NormalizedMeasurement, NormalizedObservation
from api_sentinel.ingestion.quarantine import QuarantineEntry
from api_sentinel.reliability.slo import SloAssessment, SloDefinition


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


#: SQLite's documented bind-parameter ceiling is 999 (modern builds raise it
#: far higher); chunked `IN` lookups stay well below either limit so the
#: same code path works on every supported sqlite version.
_BIND_CHUNK = 400


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

    def get_many(self, service_ids: Collection[str]) -> dict[str, ApiService]:
        """Batched service lookup — one chunked `IN` query per 400 ids.

        C10-F006: the ingest assess stage used to call `get` once per item;
        batch callers now resolve every candidate id in O(#ids/400) queries.
        """
        ids = sorted(set(service_ids))
        found: dict[str, ApiService] = {}
        for off in range(0, len(ids), _BIND_CHUNK):
            chunk = ids[off : off + _BIND_CHUNK]
            marks = ",".join("?" for _ in chunk)
            rows = self._c.execute(
                f"SELECT * FROM api_services WHERE service_id IN ({marks})",  # noqa: S608 — `marks` is a literal run of `?` bind placeholders; all values are parameters
                chunk,
            ).fetchall()
            for r in rows:
                svc = self._row(r)
                found[svc.service_id] = svc
        return found

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
                s.service_id,
                s.service_name,
                s.environment_id,
                s.namespace,
                s.display_name,
                json.dumps([x.model_dump() for x in s.external_refs]),
                s.created_at.isoformat(),
                s.updated_at.isoformat(),
            ),
        )
        return s


class _EnvironmentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def get_by_name(self, name: str) -> ServiceEnvironment | None:
        r = self._c.execute("SELECT * FROM service_environments WHERE name=?", (name,)).fetchone()
        if not r:
            return None
        return ServiceEnvironment(
            environment_id=r["environment_id"],
            name=r["name"],
            scope=r["scope"],
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
            source_id=r["source_id"],
            kind=r["kind"],
            service_id=r["service_id"],
            enabled=bool(r["enabled"]),
            config=json.loads(r["config"]),
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
            (
                s.source_id,
                s.kind,
                s.service_id,
                int(s.enabled),
                json.dumps(s.config),
                s.created_at.isoformat(),
            ),
        )
        return s


class _OperationRepo:
    """Declared operation inventory — reads the api_operations table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> ApiOperation:
        return ApiOperation(
            operation_id=r["operation_id"],
            service_id=r["service_id"],
            identity=OperationIdentity(
                protocol=r["protocol"], method=r["method"], route=r["route"]
            ),
            normalized_route=r["normalized_route"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def get(self, operation_id: str) -> ApiOperation | None:
        r = self._c.execute(
            "SELECT * FROM api_operations WHERE operation_id=?", (operation_id,)
        ).fetchone()
        return self._row(r) if r else None

    def list(self, service_id: str | None = None) -> list[ApiOperation]:
        if service_id:
            rows = self._c.execute(
                "SELECT * FROM api_operations WHERE service_id=? ORDER BY operation_id",
                (service_id,),
            )
        else:
            rows = self._c.execute("SELECT * FROM api_operations ORDER BY service_id, operation_id")
        return [self._row(r) for r in rows]

    def put(self, o: ApiOperation) -> ApiOperation:
        self._c.execute(
            "INSERT OR REPLACE INTO api_operations"
            " (operation_id, service_id, protocol, method, route, normalized_route,"
            "  created_at) VALUES (?,?,?,?,?,?,?)",
            (
                o.operation_id,
                o.service_id,
                o.identity.protocol,
                o.identity.method,
                o.identity.route,
                o.normalized_route,
                o.created_at.isoformat(),
            ),
        )
        return o


class _ReleaseRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> ServiceRelease:
        return ServiceRelease(
            release_id=r["release_id"],
            service_id=r["service_id"],
            version=r["version"],
            deployed_at=(datetime.fromisoformat(r["deployed_at"]) if r["deployed_at"] else None),
            source=r["source"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def put(self, r: ServiceRelease) -> ServiceRelease:
        self._c.execute(
            "INSERT OR REPLACE INTO service_releases"
            " (release_id, service_id, version, deployed_at, source, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                r.release_id,
                r.service_id,
                r.version,
                r.deployed_at.isoformat() if r.deployed_at else None,
                r.source,
                r.created_at.isoformat(),
            ),
        )
        return r

    def list(self, service_id: str | None = None) -> builtins.list[ServiceRelease]:
        if service_id:
            rows = self._c.execute(
                "SELECT * FROM service_releases WHERE service_id=?"
                " ORDER BY deployed_at, release_id",
                (service_id,),
            ).fetchall()
        else:
            rows = self._c.execute(
                "SELECT * FROM service_releases ORDER BY deployed_at, release_id"
            ).fetchall()
        return [self._row(r) for r in rows]


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
            (
                b.batch_id,
                b.provenance.source_id,
                b.provenance.observed_at.isoformat(),
                b.provenance.received_at.isoformat(),
                json.dumps([s.value for s in b.signal_types]),
                b.quality.value,
                b.item_count,
                b.idempotency_key,
                b.provenance.adapter_version,
                b.provenance.transform_notes,
            ),
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
            evidence_id=r["evidence_id"],
            kind=r["kind"],
            created_at=datetime.fromisoformat(r["created_at"]),
            hash=r["hash"],
            size_bytes=r["size_bytes"],
            truncated=bool(r["truncated"]),
            loss_notes=r["loss_notes"],
            source_id=r["source_id"],
            blob_ref=r["blob_ref"],
        )

    def put(self, a: EvidenceArtifact) -> EvidenceArtifact:
        self._c.execute(
            "INSERT OR REPLACE INTO evidence_artifacts"
            " (evidence_id, kind, created_at, hash, size_bytes, truncated, loss_notes, source_id,"
            "  blob_ref) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                a.evidence_id,
                a.kind,
                a.created_at.isoformat(),
                a.hash,
                a.size_bytes,
                int(a.truncated),
                a.loss_notes,
                a.source_id,
                a.blob_ref,
            ),
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
            (
                e.audit_id,
                e.at.isoformat(),
                e.actor,
                e.action,
                e.target_type,
                e.target_id,
                json.dumps(e.details),
            ),
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
                audit_id=r["audit_id"],
                at=datetime.fromisoformat(r["at"]),
                actor=r["actor"],
                action=r["action"],
                target_type=r["target_type"],
                target_id=r["target_id"],
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
            source_id=r["source_id"],
            checkpoint=r["checkpoint"],
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

    def idempotency_keys(self, observation_ids: Collection[str]) -> dict[str, str]:
        """Batched dedup read: observation_id → idempotency_key.

        C10-F006: the ingest dedup check used to run one point `get` per
        candidate; batch callers now resolve all ids in O(#ids/400)
        queries. Only the key column is fetched — dedup needs the stored
        key, never the row payload.
        """
        ids = sorted(set(observation_ids))
        found: dict[str, str] = {}
        for off in range(0, len(ids), _BIND_CHUNK):
            chunk = ids[off : off + _BIND_CHUNK]
            marks = ",".join("?" for _ in chunk)
            rows = self._c.execute(
                "SELECT observation_id, idempotency_key FROM runtime_observations"  # noqa: S608 — `marks` below is a literal run of `?` bind placeholders; all values are parameters
                f" WHERE observation_id IN ({marks})",
                chunk,
            ).fetchall()
            for r in rows:
                found[r["observation_id"]] = r["idempotency_key"]
        return found

    def put(self, o: NormalizedObservation) -> None:
        self._c.execute(
            "INSERT OR IGNORE INTO runtime_observations"
            " (observation_id, service_id, operation_id, release_id, window_start,"
            "  window_end, ingest_time, processing_time, source_id, batch_id, quality,"
            "  measurement, attributes, idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                o.observation_id,
                o.service_id,
                o.operation_id,
                o.release_id,
                o.window_start.isoformat(),
                o.window_end.isoformat(),
                o.ingest_time.isoformat(),
                o.processing_time.isoformat(),
                o.source_id,
                o.batch_id,
                o.quality.value,
                o.measurement.model_dump_json(),
                json.dumps(o.attributes, default=str),
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

    def overlapping_sources_in_ranges(
        self,
        service_id: str,
        windows: Collection[tuple[datetime, datetime]],
        exclude_source_id: str,
    ) -> set[str]:
        """Union of `overlapping_sources` over many windows — one SQL scan.

        C10-F006: callers used to issue the per-window query once per
        distinct batch window (2x1440 on the dispersed benchmark). This
        reads the bounding range once and matches each stored row against
        the exact window set in memory: a row overlaps query window ``w``
        iff ``row.window_start < w.end AND row.window_end > w.start`` —
        identical semantics, never a bounding-box over-approximation (a
        source covering only the gap between two windows is NOT reported).

        Matching runs O(#windows · log) via the sorted window starts plus
        a running max of window ends: a stored row ``(s, e)`` overlaps iff
        the earliest-ending-adequate prefix contains a window whose end
        exceeds ``s``.
        """
        wins = sorted(set(windows))
        if not wins:
            return set()
        starts = [w[0] for w in wins]
        # prefix_max_end[i] = max window_end over wins[0..i]; with bisect on
        # `starts` it answers "any window with start < e has end > s" in
        # O(log n) per stored row.
        prefix_max_end = list(accumulate((w[1] for w in wins), max))
        rows = self._c.execute(
            "SELECT DISTINCT source_id, window_start, window_end"
            " FROM runtime_observations"
            " WHERE service_id=? AND source_id!=?"
            "   AND window_start < ? AND window_end > ?",
            (
                service_id,
                exclude_source_id,
                wins[-1][1].isoformat(),  # bounding window_end bound (hi)
                wins[0][0].isoformat(),  # bounding window_start bound (lo)
            ),
        ).fetchall()
        out: set[str] = set()
        for r in rows:
            row_start = datetime.fromisoformat(r["window_start"])
            row_end = datetime.fromisoformat(r["window_end"])
            idx = bisect_left(starts, row_end)  # wins[i].start < row_end
            if idx and prefix_max_end[idx - 1] > row_start:
                out.add(r["source_id"])
        return out

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
                e.item_id,
                e.batch_id,
                e.reason,
                e.payload,
                int(e.payload_truncated),
                e.created_at.isoformat(),
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
                "SELECT * FROM slo_definitions WHERE slo_id=? ORDER BY revision DESC LIMIT 1",
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
                "SELECT * FROM slo_definitions WHERE service_id=? ORDER BY slo_id, revision",
                (service_id,),
            )
        else:
            rows = self._c.execute("SELECT * FROM slo_definitions ORDER BY slo_id, revision")
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
                a.assessment_id,
                a.slo_id,
                a.revision,
                a.window_start.isoformat(),
                a.window_end.isoformat(),
                a.sli,
                a.eligible,
                a.good,
                a.budget_consumed,
                a.state.value,
                a.model_dump_json(),
                a.evaluated_at.isoformat(),
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

    def list_for_service(self, service_id: str) -> builtins.list[SloAssessment]:
        # service lives on the immutable definition; join on (slo_id, revision)
        rows = self._c.execute(
            "SELECT a.* FROM slo_assessments a"
            " JOIN slo_definitions d"
            "   ON d.slo_id = a.slo_id AND d.revision = a.revision"
            " WHERE d.service_id=?"
            " ORDER BY a.window_start, a.assessment_id",
            (service_id,),
        ).fetchall()
        return [self._row(r) for r in rows]


class _DetectionRuleRepo:
    """Immutable per (rule_id, revision) — INSERT only, like SLO definitions."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> DetectionRule:
        return DetectionRule(
            rule_id=r["rule_id"],
            revision=r["revision"],
            service_id=r["service_id"],
            kind=r["kind"],
            config=json.loads(r["config"]),
            enabled=bool(r["enabled"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def get(self, rule_id: str, revision: int | None = None) -> DetectionRule | None:
        if revision is None:
            r = self._c.execute(
                "SELECT * FROM detection_rules WHERE rule_id=? ORDER BY revision DESC LIMIT 1",
                (rule_id,),
            ).fetchone()
        else:
            r = self._c.execute(
                "SELECT * FROM detection_rules WHERE rule_id=? AND revision=?",
                (rule_id, revision),
            ).fetchone()
        return self._row(r) if r else None

    def list(self, service_id: str | None = None) -> list[DetectionRule]:
        if service_id:
            rows = self._c.execute(
                "SELECT * FROM detection_rules WHERE service_id=? ORDER BY rule_id, revision",
                (service_id,),
            )
        else:
            rows = self._c.execute("SELECT * FROM detection_rules ORDER BY rule_id, revision")
        return [self._row(r) for r in rows]

    def put(self, rule: DetectionRule) -> None:
        # INSERT only: a revision is immutable; conflicting re-define is a
        # ConflictError handled by the caller (rule changes never rewrite
        # historical results without an explicit revision).
        self._c.execute(
            "INSERT INTO detection_rules"
            " (rule_id, revision, service_id, kind, config, enabled, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                rule.rule_id,
                rule.revision,
                rule.service_id,
                rule.kind,
                json.dumps(rule.config, default=str),
                int(rule.enabled),
                rule.created_at.isoformat(),
            ),
        )


class _AnomalyRepo:
    """Deterministic anomaly ids — INSERT OR IGNORE makes replays idempotent."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> Anomaly:
        return Anomaly.model_validate(json.loads(r["anomaly"]))

    def get(self, anomaly_id: str) -> Anomaly | None:
        r = self._c.execute("SELECT * FROM anomalies WHERE anomaly_id=?", (anomaly_id,)).fetchone()
        return self._row(r) if r else None

    def put(self, anomaly: Anomaly) -> None:
        self._c.execute(
            "INSERT OR IGNORE INTO anomalies"
            " (anomaly_id, service_id, operation_id, detected_at, window_start,"
            "  window_end, rule_id, rule_revision, detector_id, detector_version,"
            "  severity, status, explanation, evidence_refs, unknowns, dedup_key,"
            "  anomaly, incident_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                anomaly.anomaly_id,
                anomaly.service_id,
                anomaly.operation_id,
                anomaly.detected_at.isoformat(),
                anomaly.window_start.isoformat(),
                anomaly.window_end.isoformat(),
                anomaly.rule.rule_id,
                anomaly.rule.rule_revision,
                anomaly.rule.detector_id,
                anomaly.rule.detector_version,
                anomaly.severity.value,
                anomaly.status.value,
                anomaly.explanation.model_dump_json(),
                json.dumps(anomaly.evidence_refs),
                json.dumps(anomaly.unknowns),
                anomaly.dedup_key,
                anomaly.model_dump_json(),
                anomaly.incident_id,
            ),
        )

    def attach_incident(self, anomaly_id: str, incident_id: str) -> None:
        # Write-once dedup link; the anomaly row's own content is preserved.
        anomaly = self.get(anomaly_id)
        if anomaly is None:
            raise StorageError(f"anomaly not found: {anomaly_id}")
        if anomaly.incident_id is not None:
            return  # already attached — never rewrites
        linked = anomaly.model_copy(update={"incident_id": incident_id})
        self._c.execute(
            "UPDATE anomalies SET incident_id=?, anomaly=? WHERE anomaly_id=?"
            " AND incident_id IS NULL",
            (incident_id, linked.model_dump_json(), anomaly_id),
        )

    def list_for_service(self, service_id: str) -> list[Anomaly]:
        rows = self._c.execute(
            "SELECT * FROM anomalies WHERE service_id=? ORDER BY detected_at, anomaly_id",
            (service_id,),
        )
        return [self._row(r) for r in rows]

    def list_for_incident(self, incident_id: str) -> list[Anomaly]:
        rows = self._c.execute(
            "SELECT * FROM anomalies WHERE incident_id=? ORDER BY detected_at, anomaly_id",
            (incident_id,),
        )
        return [self._row(r) for r in rows]

    def list_filtered(
        self,
        service_id: str | None = None,
        status: AnomalyStatus | None = None,
        limit: int | None = None,
    ) -> list[Anomaly]:
        """Feed read — most recent first, optionally bounded."""
        sql = "SELECT * FROM anomalies"
        args: list[Any] = []
        clauses: list[str] = []
        if service_id is not None:
            clauses.append("service_id=?")
            args.append(service_id)
        if status is not None:
            clauses.append("status=?")
            args.append(status.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY detected_at DESC, anomaly_id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [self._row(r) for r in self._c.execute(sql, args).fetchall()]


class _IncidentRepo:
    """Current-state rows; `expected_revision` is the concurrency guard."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> Incident:
        return Incident.model_validate(json.loads(r["state"]))

    def get(self, incident_id: str) -> Incident | None:
        r = self._c.execute(
            "SELECT * FROM incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        return self._row(r) if r else None

    def put(self, incident: Incident, expected_revision: int | None = None) -> None:
        if expected_revision is None:
            self._c.execute(
                "INSERT INTO incidents"
                " (incident_id, title, severity, status, service_id, operation_id,"
                "  group_key, first_observed_at, detected_at, acknowledged_at,"
                "  resolved_at, owner, revision, state)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                self._cols(incident),
            )
            return
        cur = self._c.execute(
            "UPDATE incidents SET title=?, severity=?, status=?, service_id=?,"
            " operation_id=?, group_key=?, first_observed_at=?, detected_at=?,"
            " acknowledged_at=?, resolved_at=?, owner=?, revision=?, state=?"
            " WHERE incident_id=? AND revision=?",
            (
                incident.title,
                incident.severity.value,
                incident.status.value,
                incident.service_id,
                incident.operation_id,
                incident.group_key,
                incident.first_observed_at.isoformat(),
                incident.detected_at.isoformat(),
                incident.acknowledged_at.isoformat() if incident.acknowledged_at else None,
                incident.resolved_at.isoformat() if incident.resolved_at else None,
                incident.owner,
                incident.revision,
                incident.model_dump_json(),
                incident.incident_id,
                expected_revision,
            ),
        )
        if cur.rowcount == 0:
            raise ConflictError(
                "incident revision conflict (concurrent modification or stale"
                f" read): {incident.incident_id}",
                details={
                    "incident_id": incident.incident_id,
                    "expected_revision": expected_revision,
                },
            )

    @staticmethod
    def _cols(i: Incident) -> tuple[object, ...]:
        return (
            i.incident_id,
            i.title,
            i.severity.value,
            i.status.value,
            i.service_id,
            i.operation_id,
            i.group_key,
            i.first_observed_at.isoformat(),
            i.detected_at.isoformat(),
            i.acknowledged_at.isoformat() if i.acknowledged_at else None,
            i.resolved_at.isoformat() if i.resolved_at else None,
            i.owner,
            i.revision,
            i.model_dump_json(),
        )

    def list(
        self,
        status: IncidentStatus | None = None,
        service_id: str | None = None,
    ) -> list[Incident]:
        sql = "SELECT * FROM incidents"
        args: list[Any] = []
        clauses: list[str] = []
        if status is not None:
            clauses.append("status=?")
            args.append(status.value)
        if service_id is not None:
            clauses.append("service_id=?")
            args.append(service_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._c.execute(sql + " ORDER BY detected_at DESC, incident_id", args).fetchall()
        return [self._row(r) for r in rows]

    def find_open(self, service_id: str, group_key: str) -> Incident | None:
        row = self._c.execute(
            "SELECT * FROM incidents WHERE service_id=? AND group_key=?"
            " AND status IN"
            " ('DETECTED','TRIAGED','INVESTIGATING','MITIGATING','MONITORING',"
            "  'REOPENED')"
            " ORDER BY detected_at DESC, incident_id LIMIT 1",
            (service_id, group_key),
        ).fetchone()
        return self._row(row) if row else None


class _IncidentEventRepo:
    """The journal — append-only by design: no update/delete paths exist."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> IncidentEvent:
        return IncidentEvent(
            event_id=r["event_id"],
            incident_id=r["incident_id"],
            at=datetime.fromisoformat(r["at"]),
            actor=r["actor"],
            kind=r["kind"],
            from_status=(IncidentStatus(r["from_status"]) if r["from_status"] else None),
            to_status=(IncidentStatus(r["to_status"]) if r["to_status"] else None),
            note=r["note"],
            details=json.loads(r["details"]),
        )

    def append(self, event: IncidentEvent) -> None:
        # Deterministic event ids: a replayed detection run is INSERT OR
        # IGNORE — a no-op, never a duplicated journal entry.
        self._c.execute(
            "INSERT OR IGNORE INTO incident_events"
            " (event_id, incident_id, at, actor, kind, from_status, to_status,"
            "  note, details) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event.event_id,
                event.incident_id,
                event.at.isoformat(),
                event.actor,
                event.kind.value,
                event.from_status.value if event.from_status else None,
                event.to_status.value if event.to_status else None,
                event.note,
                json.dumps(event.details),
            ),
        )

    def list(self, incident_id: str) -> list[IncidentEvent]:
        # Journal order is append order: the timeline is read in insertion
        # sequence (rowid), not re-sorted by timestamp — two events may share
        # the same recorded `at` under a fixed clock.
        rows = self._c.execute(
            "SELECT * FROM incident_events WHERE incident_id=? ORDER BY rowid",
            (incident_id,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def count(self, incident_id: str) -> int:
        return int(
            self._c.execute(
                "SELECT COUNT(*) FROM incident_events WHERE incident_id=?",
                (incident_id,),
            ).fetchone()[0]
        )


class _ChangeEventRepo:
    """Deterministic change ids — INSERT OR IGNORE makes re-imports idempotent."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> ChangeEvent:
        return ChangeEvent(
            change_id=r["change_id"],
            service_id=r["service_id"],
            kind=r["kind"],
            at=datetime.fromisoformat(r["at"]),
            source=r["source"],
            ref=r["ref"],
            details=json.loads(r["details"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def get(self, change_id: str) -> ChangeEvent | None:
        r = self._c.execute(
            "SELECT * FROM change_events WHERE change_id=?", (change_id,)
        ).fetchone()
        return self._row(r) if r else None

    def put(self, event: ChangeEvent) -> None:
        self._c.execute(
            "INSERT OR IGNORE INTO change_events"
            " (change_id, service_id, kind, at, source, ref, details, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                event.change_id,
                event.service_id,
                event.kind.value,
                event.at.isoformat(),
                event.source,
                event.ref,
                json.dumps(event.details),
                event.created_at.isoformat(),
            ),
        )

    def list_for_service(
        self,
        service_id: str,
        at_start: datetime | None = None,
        at_end: datetime | None = None,
    ) -> list[ChangeEvent]:
        sql = "SELECT * FROM change_events WHERE service_id=?"
        args: list[Any] = [service_id]
        if at_start is not None:
            sql += " AND at >= ?"
            args.append(at_start.isoformat())
        if at_end is not None:
            sql += " AND at <= ?"
            args.append(at_end.isoformat())
        sql += " ORDER BY at, change_id"
        rows = self._c.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]


class _DependencyObservationRepo:
    """The operational graph — deterministic edge ids, re-ingest accumulates
    counters in place rather than duplicating rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> DependencyObservation:
        return DependencyObservation(
            edge_id=r["edge_id"],
            service_id=r["service_id"],
            target=r["target"],
            target_kind=r["target_kind"],
            operation_id=r["operation_id"],
            window_start=datetime.fromisoformat(r["window_start"]),
            window_end=datetime.fromisoformat(r["window_end"]),
            calls=int(r["calls"]),
            errors=int(r["errors"]),
            timeouts=int(r["timeouts"]),
            evidence_refs=json.loads(r["evidence_refs"]),
            provenance=r["provenance"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def get(self, edge_id: str) -> DependencyObservation | None:
        r = self._c.execute(
            "SELECT * FROM dependency_observations WHERE edge_id=?",
            (edge_id,),
        ).fetchone()
        return self._row(r) if r else None

    def put(self, edge: DependencyObservation) -> None:
        self._c.execute(
            "INSERT INTO dependency_observations"
            " (edge_id, service_id, target, target_kind, operation_id,"
            "  window_start, window_end, calls, errors, timeouts,"
            "  evidence_refs, provenance, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(edge_id) DO UPDATE SET"
            "   calls=calls+excluded.calls,"
            "   errors=errors+excluded.errors,"
            "   timeouts=timeouts+excluded.timeouts",
            (
                edge.edge_id,
                edge.service_id,
                edge.target,
                edge.target_kind,
                edge.operation_id,
                edge.window_start.isoformat(),
                edge.window_end.isoformat(),
                edge.calls,
                edge.errors,
                edge.timeouts,
                json.dumps(edge.evidence_refs),
                edge.provenance,
                edge.created_at.isoformat(),
            ),
        )

    def _list(
        self,
        column: str,
        args: list[Any],
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> list[DependencyObservation]:
        # `column` is an internal literal — 'service_id' or 'target' — never
        # user input; the value goes through `args` as a bound parameter.
        if column not in ("service_id", "target"):
            raise StorageError(f"unexpected dep-observation column {column!r}")
        sql = f"SELECT * FROM dependency_observations WHERE {column}=?"  # noqa: S608
        if window_start is not None:
            sql += " AND window_end >= ?"
            args.append(window_start.isoformat())
        if window_end is not None:
            sql += " AND window_start <= ?"
            args.append(window_end.isoformat())
        sql += " ORDER BY window_start, edge_id"
        return [self._row(r) for r in self._c.execute(sql, args).fetchall()]

    def list_for_service(
        self,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[DependencyObservation]:
        return self._list("service_id", [service_id], window_start, window_end)

    def list_involving(
        self,
        target: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[DependencyObservation]:
        return self._list("target", [target], window_start, window_end)


class _HandoffRecordRepo:
    """One row per handoff; status updates keep the original request_ref."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> ExternalHandoff:
        return ExternalHandoff(
            handoff_id=r["handoff_id"],
            to_forge=r["to_forge"],
            kind=r["kind"],
            status=HandoffStatus(r["status"]),
            request_ref=r["request_ref"],
            response_ref=r["response_ref"],
            detail=r["detail"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def get(self, handoff_id: str) -> ExternalHandoff | None:
        r = self._c.execute(
            "SELECT * FROM handoff_records WHERE handoff_id=?", (handoff_id,)
        ).fetchone()
        return self._row(r) if r else None

    def put(self, record: ExternalHandoff) -> None:
        self._c.execute(
            "INSERT INTO handoff_records"
            " (handoff_id, to_forge, kind, status, request_ref, response_ref,"
            "  detail, created_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(handoff_id) DO UPDATE SET"
            "   status=excluded.status,"
            "   response_ref=excluded.response_ref,"
            "   detail=excluded.detail",
            (
                record.handoff_id,
                record.to_forge,
                record.kind,
                record.status.value,
                record.request_ref,
                record.response_ref,
                record.detail,
                record.created_at.isoformat(),
            ),
        )

    def list(
        self,
        to_forge: str | None = None,
        status: HandoffStatus | None = None,
    ) -> list[ExternalHandoff]:
        sql = "SELECT * FROM handoff_records"
        args: list[Any] = []
        clauses: list[str] = []
        if to_forge is not None:
            clauses.append("to_forge=?")
            args.append(to_forge)
        if status is not None:
            clauses.append("status=?")
            args.append(status.value)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._c.execute(sql + " ORDER BY created_at, handoff_id", args).fetchall()
        return [self._row(r) for r in rows]


class _InvestigationRepo:
    """Investigation audit rows — status/result updates in place."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> Investigation:
        return Investigation(
            investigation_id=r["investigation_id"],
            incident_id=r["incident_id"],
            status=InvestigationStatus(r["status"]),
            result=json.loads(r["result"]),
            context_pack=json.loads(r["context_pack"]),
            idempotency_key=r["idempotency_key"],
            created_at=datetime.fromisoformat(r["created_at"]),
            finished_at=(datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else None),
        )

    def get(self, investigation_id: str) -> Investigation | None:
        r = self._c.execute(
            "SELECT * FROM investigations WHERE investigation_id=?",
            (investigation_id,),
        ).fetchone()
        return self._row(r) if r else None

    def get_by_idempotency_key(self, key: str) -> Investigation | None:
        r = self._c.execute(
            "SELECT * FROM investigations WHERE idempotency_key=?", (key,)
        ).fetchone()
        return self._row(r) if r else None

    def put(self, i: Investigation) -> None:
        self._c.execute(
            "INSERT INTO investigations"
            " (investigation_id, incident_id, status, result, context_pack,"
            "  idempotency_key, created_at, finished_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(investigation_id) DO UPDATE SET"
            "   status=excluded.status,"
            "   result=excluded.result,"
            "   context_pack=excluded.context_pack,"
            "   finished_at=excluded.finished_at",
            (
                i.investigation_id,
                i.incident_id,
                i.status.value,
                json.dumps(i.result, default=str),
                json.dumps(i.context_pack, default=str),
                i.idempotency_key,
                i.created_at.isoformat(),
                i.finished_at.isoformat() if i.finished_at else None,
            ),
        )

    def list_for_incident(self, incident_id: str) -> list[Investigation]:
        rows = self._c.execute(
            "SELECT * FROM investigations WHERE incident_id=?"
            " ORDER BY created_at, investigation_id",
            (incident_id,),
        ).fetchall()
        return [self._row(r) for r in rows]


class _IngestionJobRepo:
    """HTTP-initiated import audit rows — idempotency-key dedup."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _row(self, r: sqlite3.Row) -> IngestionJob:
        return IngestionJob(
            job_id=r["job_id"],
            source_id=r["source_id"],
            file_ref=r["file_ref"],
            kind=r["kind"],
            status=IngestionJobStatus(r["status"]),
            accepted=int(r["accepted"]),
            quarantined=int(r["quarantined"]),
            skipped_duplicates=int(r["skipped_duplicates"]),
            batch_id=r["batch_id"],
            error=r["error"],
            idempotency_key=r["idempotency_key"],
            created_at=datetime.fromisoformat(r["created_at"]),
            finished_at=(datetime.fromisoformat(r["finished_at"]) if r["finished_at"] else None),
        )

    def get(self, job_id: str) -> IngestionJob | None:
        r = self._c.execute("SELECT * FROM ingestion_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row(r) if r else None

    def get_by_idempotency_key(self, key: str) -> IngestionJob | None:
        r = self._c.execute(
            "SELECT * FROM ingestion_jobs WHERE idempotency_key=?", (key,)
        ).fetchone()
        return self._row(r) if r else None

    def put(self, j: IngestionJob) -> None:
        self._c.execute(
            "INSERT INTO ingestion_jobs"
            " (job_id, source_id, file_ref, kind, status, accepted, quarantined,"
            "  skipped_duplicates, batch_id, error, idempotency_key,"
            "  created_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(job_id) DO UPDATE SET"
            "   status=excluded.status,"
            "   accepted=excluded.accepted,"
            "   quarantined=excluded.quarantined,"
            "   skipped_duplicates=excluded.skipped_duplicates,"
            "   batch_id=excluded.batch_id,"
            "   error=excluded.error,"
            "   finished_at=excluded.finished_at",
            (
                j.job_id,
                j.source_id,
                j.file_ref,
                j.kind,
                j.status.value,
                j.accepted,
                j.quarantined,
                j.skipped_duplicates,
                j.batch_id,
                j.error,
                j.idempotency_key,
                j.created_at.isoformat(),
                j.finished_at.isoformat() if j.finished_at else None,
            ),
        )


class SqliteUnitOfWork:
    """One connection, explicit commit/rollback; repos share the transaction."""

    services: ServiceRepository
    environments: EnvironmentRepository
    sources: SourceRepository
    operations: OperationRepository
    releases: ReleaseRepository
    batches: BatchRepository
    evidence: EvidenceRepository
    audit: AuditRepository
    checkpoints: CheckpointRepository
    observations: ObservationRepository
    quarantine: QuarantineRepository
    slo_definitions: SloDefinitionRepository
    slo_assessments: SloAssessmentRepository
    detection_rules: DetectionRuleRepository
    anomalies: AnomalyRepository
    incidents: IncidentRepository
    incident_events: IncidentEventRepository
    change_events: ChangeEventRepository
    dependency_observations: DependencyObservationRepository
    handoff_records: HandoffRecordRepository
    investigations: InvestigationRepository
    ingestion_jobs: IngestionJobRepository

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
        self.operations = _OperationRepo(conn)
        self.releases = _ReleaseRepo(conn)
        self.batches = _BatchRepo(conn)
        self.evidence = _EvidenceRepo(conn)
        self.audit = _AuditRepo(conn)
        self.checkpoints = _CheckpointRepo(conn)
        self.observations = _ObservationRepo(conn)
        self.quarantine = _QuarantineRepo(conn)
        self.slo_definitions = _SloDefinitionRepo(conn)
        self.slo_assessments = _SloAssessmentRepo(conn)
        self.detection_rules = _DetectionRuleRepo(conn)
        self.anomalies = _AnomalyRepo(conn)
        self.incidents = _IncidentRepo(conn)
        self.incident_events = _IncidentEventRepo(conn)
        self.change_events = _ChangeEventRepo(conn)
        self.dependency_observations = _DependencyObservationRepo(conn)
        self.handoff_records = _HandoffRecordRepo(conn)
        self.investigations = _InvestigationRepo(conn)
        self.ingestion_jobs = _IngestionJobRepo(conn)
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
