"""Repository/store ports — the hexagonal boundary of the core."""

from __future__ import annotations

import builtins
from collections.abc import Collection
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from api_sentinel.application.jobs import IngestionJob, Investigation
from api_sentinel.detection.protocol import Anomaly, AnomalyStatus
from api_sentinel.detection.registry import DetectionRule
from api_sentinel.domain import (
    ApiOperation,
    ApiService,
    AuditEvent,
    ChangeEvent,
    DependencyObservation,
    EvidenceArtifact,
    ExternalHandoff,
    HandoffStatus,
    IngestionCheckpoint,
    ObservationBatch,
    ServiceEnvironment,
    ServiceRelease,
    TelemetrySource,
)
from api_sentinel.incidents.journal import IncidentEvent
from api_sentinel.incidents.models import Incident, IncidentStatus
from api_sentinel.ingestion.models import NormalizedObservation
from api_sentinel.ingestion.quarantine import QuarantineEntry
from api_sentinel.reliability.slo import SloAssessment, SloDefinition


@runtime_checkable
class ServiceRepository(Protocol):
    def get(self, service_id: str) -> ApiService | None: ...
    def get_many(self, service_ids: Collection[str]) -> dict[str, ApiService]: ...
    def get_by_name(self, environment_id: str, service_name: str) -> ApiService | None: ...
    def list(self) -> list[ApiService]: ...
    def put(self, service: ApiService) -> ApiService: ...


@runtime_checkable
class EnvironmentRepository(Protocol):
    def get_by_name(self, name: str) -> ServiceEnvironment | None: ...
    def put(self, env: ServiceEnvironment) -> ServiceEnvironment: ...


@runtime_checkable
class SourceRepository(Protocol):
    def get(self, source_id: str) -> TelemetrySource | None: ...
    def list(self, service_id: str | None = None) -> list[TelemetrySource]: ...
    def put(self, source: TelemetrySource) -> TelemetrySource: ...


@runtime_checkable
class OperationRepository(Protocol):
    """Declared operation inventory (api_operations) — observed coverage is
    derived from runtime_observations, never merged silently."""

    def get(self, operation_id: str) -> ApiOperation | None: ...
    def list(self, service_id: str | None = None) -> list[ApiOperation]: ...
    def put(self, operation: ApiOperation) -> ApiOperation: ...


@runtime_checkable
class ReleaseRepository(Protocol):
    def put(self, release: ServiceRelease) -> ServiceRelease: ...
    def list(self, service_id: str | None = None) -> builtins.list[ServiceRelease]: ...


@runtime_checkable
class BatchRepository(Protocol):
    def get_by_idempotency_key(self, key: str) -> ObservationBatch | None: ...
    def put(self, batch: ObservationBatch) -> ObservationBatch: ...


@runtime_checkable
class EvidenceRepository(Protocol):
    def get(self, evidence_id: str) -> EvidenceArtifact | None: ...
    def put(self, artifact: EvidenceArtifact) -> EvidenceArtifact: ...


@runtime_checkable
class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> None: ...
    def list(self, target_id: str | None = None) -> list[AuditEvent]: ...


@runtime_checkable
class CheckpointRepository(Protocol):
    def get(self, source_id: str) -> IngestionCheckpoint | None: ...
    def put(self, checkpoint: IngestionCheckpoint) -> None: ...


@runtime_checkable
class ObservationRepository(Protocol):
    """Append-only: identical content is a no-op, conflicting ids never rewrite."""

    def get(self, observation_id: str) -> NormalizedObservation | None: ...
    def idempotency_keys(
        self, observation_ids: Collection[str]
    ) -> dict[str, str]: ...
    def put(self, observation: NormalizedObservation) -> None: ...
    def list_by_batch(self, batch_id: str) -> list[NormalizedObservation]: ...
    def count(self) -> int: ...
    def aggregates(self) -> dict[str, Any]: ...
    def overlapping_sources(
        self,
        service_id: str,
        window_start: datetime,
        window_end: datetime,
        exclude_source_id: str,
    ) -> set[str]: ...
    def overlapping_sources_in_ranges(
        self,
        service_id: str,
        windows: Collection[tuple[datetime, datetime]],
        exclude_source_id: str,
    ) -> set[str]: ...
    def raw_attributes(self, observation_id: str) -> str: ...
    def list_for_service(
        self,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[NormalizedObservation]: ...


@runtime_checkable
class QuarantineRepository(Protocol):
    def put(self, entry: QuarantineEntry) -> None: ...
    def list(self, batch_id: str | None = None) -> list[QuarantineEntry]: ...
    def count(self) -> int: ...


@runtime_checkable
class SloDefinitionRepository(Protocol):
    """Versioned SLO definitions — immutable per (slo_id, revision)."""

    def get(self, slo_id: str, revision: int | None = None) -> SloDefinition | None: ...
    def list(self, service_id: str | None = None) -> list[SloDefinition]: ...
    def put(self, definition: SloDefinition) -> None: ...


@runtime_checkable
class SloAssessmentRepository(Protocol):
    """Windowed assessments — re-evaluation replaces, never duplicates."""

    def put(self, assessment: SloAssessment) -> None: ...
    def get(self, assessment_id: str) -> SloAssessment | None: ...
    def list(
        self, slo_id: str, revision: int | None = None
    ) -> list[SloAssessment]: ...
    def list_for_service(self, service_id: str) -> builtins.list[SloAssessment]: ...


@runtime_checkable
class DetectionRuleRepository(Protocol):
    """Versioned rules — immutable per (rule_id, revision), like SLOs."""

    def get(self, rule_id: str, revision: int | None = None) -> DetectionRule | None: ...
    def list(self, service_id: str | None = None) -> list[DetectionRule]: ...
    def put(self, rule: DetectionRule) -> None: ...


@runtime_checkable
class AnomalyRepository(Protocol):
    """Anomaly store — deterministic ids make writes idempotent."""

    def get(self, anomaly_id: str) -> Anomaly | None: ...
    def put(self, anomaly: Anomaly) -> None: ...
    def attach_incident(self, anomaly_id: str, incident_id: str) -> None: ...
    def list_for_service(self, service_id: str) -> list[Anomaly]: ...
    def list_for_incident(self, incident_id: str) -> list[Anomaly]: ...
    def list_filtered(
        self,
        service_id: str | None = None,
        status: AnomalyStatus | None = None,
        limit: int | None = None,
    ) -> list[Anomaly]: ...


@runtime_checkable
class IncidentRepository(Protocol):
    """Current-state rows (ADR-008); `expected_revision` enforces concurrency."""

    def get(self, incident_id: str) -> Incident | None: ...
    def put(self, incident: Incident, expected_revision: int | None = None) -> None: ...
    def list(
        self,
        status: IncidentStatus | None = None,
        service_id: str | None = None,
    ) -> list[Incident]: ...
    def find_open(self, service_id: str, group_key: str) -> Incident | None: ...


@runtime_checkable
class IncidentEventRepository(Protocol):
    """The journal — append-only by design: no update/delete paths exist."""

    def append(self, event: IncidentEvent) -> None: ...
    def list(self, incident_id: str) -> list[IncidentEvent]: ...
    def count(self, incident_id: str) -> int: ...


@runtime_checkable
class ChangeEventRepository(Protocol):
    """Change events — deterministic ids make re-imports idempotent."""

    def get(self, change_id: str) -> ChangeEvent | None: ...
    def put(self, event: ChangeEvent) -> None: ...
    def list_for_service(
        self,
        service_id: str,
        at_start: datetime | None = None,
        at_end: datetime | None = None,
    ) -> list[ChangeEvent]: ...


@runtime_checkable
class DependencyObservationRepository(Protocol):
    """The Sentinel-owned operational graph — append-only, idempotent."""

    def get(self, edge_id: str) -> DependencyObservation | None: ...
    def put(self, edge: DependencyObservation) -> None: ...
    def list_for_service(
        self,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[DependencyObservation]: ...
    def list_involving(
        self,
        target: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[DependencyObservation]: ...


@runtime_checkable
class HandoffRecordRepository(Protocol):
    """Delegation audit — one row per handoff attempt, status transitions kept."""

    def get(self, handoff_id: str) -> ExternalHandoff | None: ...
    def put(self, record: ExternalHandoff) -> None: ...
    def list(
        self,
        to_forge: str | None = None,
        status: HandoffStatus | None = None,
    ) -> list[ExternalHandoff]: ...


@runtime_checkable
class InvestigationRepository(Protocol):
    """Investigation audit rows (migration 0006) — idempotency-key dedup."""

    def get(self, investigation_id: str) -> Investigation | None: ...
    def get_by_idempotency_key(self, key: str) -> Investigation | None: ...
    def put(self, investigation: Investigation) -> None: ...
    def list_for_incident(self, incident_id: str) -> list[Investigation]: ...


@runtime_checkable
class IngestionJobRepository(Protocol):
    """HTTP-initiated import audit rows (migration 0006) — key dedup."""

    def get(self, job_id: str) -> IngestionJob | None: ...
    def get_by_idempotency_key(self, key: str) -> IngestionJob | None: ...
    def put(self, job: IngestionJob) -> None: ...


@runtime_checkable
class UnitOfWork(Protocol):
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

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@runtime_checkable
class BlobStore(Protocol):
    def put(self, content: bytes) -> str: ...
    def get(self, blob_ref: str) -> bytes: ...
