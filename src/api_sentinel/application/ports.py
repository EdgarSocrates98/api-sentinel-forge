"""Repository/store ports — the hexagonal boundary of the core."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

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
class UnitOfWork(Protocol):
    services: ServiceRepository
    environments: EnvironmentRepository
    sources: SourceRepository
    releases: ReleaseRepository
    batches: BatchRepository
    evidence: EvidenceRepository
    audit: AuditRepository
    checkpoints: CheckpointRepository

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


@runtime_checkable
class BlobStore(Protocol):
    def put(self, content: bytes) -> str: ...
    def get(self, blob_ref: str) -> bytes: ...
