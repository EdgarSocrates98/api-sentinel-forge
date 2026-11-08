"""Domain layer — pure, deterministic, dependency-free."""

from api_sentinel.domain.clock import Clock, FixedClock, SystemClock
from api_sentinel.domain.errors import (
    CapabilityUnavailableError,
    ConflictError,
    ContractError,
    NotFoundError,
    SentinelError,
    StorageError,
    ValidationError,
    WorkspaceError,
)
from api_sentinel.domain.identity import (
    DeploymentIdentity,
    ExternalRef,
    ObservationIdentity,
    OperationIdentity,
    ServiceIdentity,
)
from api_sentinel.domain.ids import IdFactory, SequentialIdFactory, UlidIdFactory, new_ulid
from api_sentinel.domain.models import (
    ApiOperation,
    ApiService,
    AuditEvent,
    EvidenceArtifact,
    IngestionCheckpoint,
    ObservationBatch,
    Provenance,
    ServiceEnvironment,
    ServiceRelease,
    TelemetrySource,
    normalize_route,
)
from api_sentinel.domain.quality import MeasurementKind, QualityState, SignalType

__all__ = [
    "ApiOperation",
    "ApiService",
    "AuditEvent",
    "CapabilityUnavailableError",
    "Clock",
    "ConflictError",
    "ContractError",
    "DeploymentIdentity",
    "EvidenceArtifact",
    "ExternalRef",
    "FixedClock",
    "IdFactory",
    "IngestionCheckpoint",
    "MeasurementKind",
    "NotFoundError",
    "ObservationBatch",
    "ObservationIdentity",
    "OperationIdentity",
    "Provenance",
    "QualityState",
    "SentinelError",
    "SequentialIdFactory",
    "ServiceEnvironment",
    "ServiceIdentity",
    "ServiceRelease",
    "SignalType",
    "StorageError",
    "SystemClock",
    "TelemetrySource",
    "UlidIdFactory",
    "ValidationError",
    "WorkspaceError",
    "new_ulid",
    "normalize_route",
]
