"""Repository/store ports — the hexagonal boundary of the core."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from api_sentinel.domain import (
    ApiService,
    AuditEvent,
    EvidenceArtifact,
    IngestionCheckpoint,
    ObservationBatch,
    ServiceEnvironment,
    ServiceRelease,
    TelemetrySource,
)
from api_sentinel.ingestion.models import NormalizedObservation
from api_sentinel.ingestion.quarantine import QuarantineEntry
from api_sentinel.reliability.slo import SloAssessment, SloDefinition


@runtime_checkable
class ServiceRepository(Protocol):
    def get(self, service_id: str) -> ApiService | None: ...
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
class ReleaseRepository(Protocol):
    def put(self, release: ServiceRelease) -> ServiceRelease: ...


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


@runtime_checkable
class UnitOfWork(Protocol):
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

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@runtime_checkable
class BlobStore(Protocol):
    def put(self, content: bytes) -> str: ...
    def get(self, blob_ref: str) -> bytes: ...
