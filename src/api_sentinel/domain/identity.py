"""Canonical identity — logical, deployment and observation kinds (ADR-012)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class ExternalRef(FrozenModel):
    """A link to an identity owned by another system, with provenance."""

    kind: str  # e.g. "cloud_resource", "code_atlas_identity", "openapi_operation_id"
    ref: str
    provenance: str = "declared"  # declared | verified | inferred


class OperationIdentity(FrozenModel):
    protocol: str = "http"
    method: str | None = None
    route: str | None = None  # normalized template, e.g. /payments/{payment_id}


class ServiceIdentity(FrozenModel):
    schema_: str = Field(default="api-sentinel/ServiceIdentity/v1", alias="schema")
    service_id: str
    environment: str
    service_name: str
    namespace: str | None = None
    display_name: str | None = None
    operation: OperationIdentity | None = None
    external_refs: list[ExternalRef] = Field(default_factory=list)


class DeploymentIdentity(FrozenModel):
    """Where/when an instance runs — never part of logical identity."""

    release_id: str | None = None
    region: str | None = None
    cluster: str | None = None
    account: str | None = None
    stage: str | None = None
    version: str | None = None
    instance_id: str | None = None


class ObservationIdentity(FrozenModel):
    """How a telemetry source labels what it saw, with provenance."""

    source_id: str
    observed_name: str
    observed_attributes: dict[str, str] = Field(default_factory=dict)
    resolved_service_id: str | None = None
    resolution: str = "unresolved"  # unresolved | resolved | ambiguous
