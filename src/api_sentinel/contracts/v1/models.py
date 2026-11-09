"""Typed envelopes mirroring contracts/v1/*.schema.json — reject unknown versions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from api_sentinel.domain import ContractError, QualityState


class Envelope(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_: str = Field(alias="schema")
    expected_schema: ClassVar[str] = ""

    @model_validator(mode="after")
    def _check_schema(self) -> Envelope:
        if self.expected_schema and self.schema_ != self.expected_schema:
            raise ContractError(
                f"unsupported schema '{self.schema_}', expected '{self.expected_schema}'",
                details={"received": self.schema_, "expected": self.expected_schema},
            )
        return self


class OperationRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: str = "http"
    method: str | None = None
    route: str | None = None


class ServiceIdentityV1(Envelope):
    expected_schema: ClassVar[str] = "api-sentinel/ServiceIdentity/v1"
    service_id: str
    environment: str
    service_name: str
    namespace: str | None = None
    display_name: str | None = None
    operation: OperationRef | None = None
    external_refs: dict[str, str | None] = Field(default_factory=dict)


class Window(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    end: datetime


class Measurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    unit: str | None = None
    value: float | None = None
    buckets: list[dict[str, Any]] | None = None
    sum: float | None = None
    count: int | None = None


class Sampling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate: float | None = Field(default=None, ge=0.0, le=1.0)
    method: str | None = None


class RuntimeObservationV1(Envelope):
    expected_schema: ClassVar[str] = "api-sentinel/RuntimeObservation/v1"
    observation_id: str
    service_id: str
    operation_id: str | None = None
    release_id: str | None = None
    window: Window
    source_id: str
    quality: QualityState = QualityState.UNKNOWN
    sampling: Sampling | None = None
    measurement: Measurement
    attributes: dict[str, Any] = Field(default_factory=dict)


class RuleRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    rule_version: str


class Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    math: str | None = None


class AnomalyV1(Envelope):
    expected_schema: ClassVar[str] = "api-sentinel/Anomaly/v1"
    anomaly_id: str
    service_id: str
    operation_id: str | None = None
    detected_at: datetime
    rule: RuleRef
    severity: str
    explanation: Explanation
    evidence_refs: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class HandoffConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget_seconds: float | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)


class InvestigationHandoffV1(Envelope):
    expected_schema: ClassVar[str] = "api-sentinel/InvestigationHandoff/v1"
    handoff_id: str
    from_forge: str = "api-sentinel-forge"
    to_forge: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    constraints: HandoffConstraints = Field(default_factory=HandoffConstraints)
    correlation_id: str | None = None


class AtlasExporter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = "code-atlas-forge"
    version: str | None = None


class AtlasService(BaseModel):
    """A structural entity owned by Code Atlas — never an observed service."""

    model_config = ConfigDict(extra="forbid")
    atlas_id: str
    name: str
    kind: str = "api_service"  # api_service | datastore | queue | external
    repo: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class AtlasEdge(BaseModel):
    """A structural relation — vocabulary mirrors Atlas EdgeKind/CALLS_ENDPOINT.

    `confidence` mirrors Atlas ConfidenceClass (resolved|syntactic|heuristic|
    inferred); `resolution` mirrors ResolutionStatus (resolved|ambiguous|
    unresolved|external); `resolver` is the producing analyzer's provenance.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    edge_id: str
    from_ref: str = Field(alias="from")
    to_ref: str = Field(alias="to")
    kind: str
    confidence: str = "syntactic"
    resolution: str = "resolved"
    resolver: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class AtlasExportV1(Envelope):
    """The versioned import contract the Atlas adapter accepts.

    Produced by `code-atlas` export tooling toward this documented shape;
    unknown versions are rejected, never defaulted (ADR-006).
    """

    expected_schema: ClassVar[str] = "api-sentinel/AtlasExport/v1"
    exported_at: datetime
    snapshot_ref: str | None = None
    exporter: AtlasExporter = Field(default_factory=AtlasExporter)
    services: list[AtlasService] = Field(default_factory=list)
    edges: list[AtlasEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> AtlasExportV1:
        """Duplicate entity ids are a contract violation, not a merge."""
        service_ids = [s.atlas_id for s in self.services]
        dup_services = sorted(
            {i for i in service_ids if service_ids.count(i) > 1}
        )
        edge_ids = [e.edge_id for e in self.edges]
        dup_edges = sorted({i for i in edge_ids if edge_ids.count(i) > 1})
        if dup_services or dup_edges:
            raise ContractError(
                "atlas export carries duplicate ids",
                details={
                    "duplicate_service_ids": dup_services,
                    "duplicate_edge_ids": dup_edges,
                },
            )
        return self


class TelemetrySourceV1(Envelope):
    expected_schema: ClassVar[str] = "api-sentinel/TelemetrySource/v1"
    source_id: str
    kind: str
    service_id: str
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class DeployRecord(BaseModel):
    """One deployment/change entry inside a `DeployManifest/v1` file.

    `service` carries a Sentinel `service_id` *or* a service name; ambiguous
    or unresolvable names are skipped with an explicit unknown, never
    name-guessed. `ref` is the opaque external pointer (sha, version,
    release id, run url).
    """

    model_config = ConfigDict(extra="forbid")
    service: str
    kind: str = "deploy"
    at: datetime
    ref: str | None = None
    details: dict[str, str] = Field(default_factory=dict)


class DeployManifestV1(Envelope):
    """Exported pipeline manifest (e.g. GitHub Actions) — a file, no creds.

    Produced by CI/export tooling toward this documented shape; unknown
    versions are rejected, never defaulted.
    """

    expected_schema: ClassVar[str] = "api-sentinel/DeployManifest/v1"
    exported_at: datetime | None = None
    exporter: str = "unknown"  # e.g. github-actions
    deploys: list[DeployRecord] = Field(default_factory=list)


class EvidenceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    retrieved_at: datetime | None = None
    tool_version: str | None = None


class EvidenceArtifactV1(Envelope):
    expected_schema: ClassVar[str] = "api-sentinel/EvidenceArtifact/v1"
    evidence_id: str
    kind: str
    created_at: datetime
    # Contract-tightened (Cycle 10): the wire schema pins this shape —
    # the model enforces it too, so a bad digest never reaches storage.
    hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    size_bytes: int = 0
    truncated: bool = False
    loss_notes: str | None = None
    provenance: EvidenceProvenance
    blob_ref: str | None = None


ENVELOPES: dict[str, type[Envelope]] = {
    cls.expected_schema: cls
    for cls in (
        ServiceIdentityV1,
        RuntimeObservationV1,
        AnomalyV1,
        InvestigationHandoffV1,
        AtlasExportV1,
        TelemetrySourceV1,
        EvidenceArtifactV1,
        DeployManifestV1,
    )
}


def parse_envelope(data: dict[str, Any]) -> Envelope:
    schema = data.get("schema")
    cls = ENVELOPES.get(schema) if isinstance(schema, str) else None
    if cls is None:
        raise ContractError(
            f"unknown contract version: {schema!r}",
            details={"supported": sorted(ENVELOPES)},
        )
    return cls.model_validate(data)
