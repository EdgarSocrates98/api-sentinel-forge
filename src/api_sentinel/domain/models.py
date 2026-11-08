"""Core domain entities (Cycle 1 subset) — frozen, dependency-free."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import Field

from api_sentinel.domain.identity import ExternalRef, FrozenModel, OperationIdentity
from api_sentinel.domain.quality import QualityState, SignalType

_PATH_PARAM = re.compile(r"/[^/{}]+")


def normalize_route(raw: str) -> str:
    """Collapse concrete path params into {name} templates for cardinality control.

    /payments/42/items/7 → /payments/{param}/items/{param}
    Already-templated segments are preserved.
    """

    parts = raw.strip().split("/")
    out = []
    for seg in parts:
        if not seg:
            continue
        if seg.startswith("{") and seg.endswith("}"):
            out.append(seg)
        elif seg.isdigit() or _looks_like_id(seg):
            out.append("{param}")
        else:
            out.append(seg)
    return "/" + "/".join(out)


def _looks_like_id(seg: str) -> bool:
    return len(seg) > 24 or bool(re.fullmatch(r"[0-9a-fA-F-]{8,}", seg))


class ApiService(FrozenModel):
    service_id: str
    service_name: str
    environment_id: str
    namespace: str | None = None
    display_name: str | None = None
    external_refs: list[ExternalRef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ServiceEnvironment(FrozenModel):
    environment_id: str
    name: str
    scope: str = "workspace"
    created_at: datetime


class ApiOperation(FrozenModel):
    operation_id: str
    service_id: str
    identity: OperationIdentity
    normalized_route: str | None = None
    created_at: datetime

    @classmethod
    def make(
        cls,
        *,
        operation_id: str,
        service_id: str,
        identity: OperationIdentity,
        created_at: datetime,
    ) -> ApiOperation:
        return cls(
            operation_id=operation_id,
            service_id=service_id,
            identity=identity,
            normalized_route=normalize_route(identity.route) if identity.route else None,
            created_at=created_at,
        )


class ServiceRelease(FrozenModel):
    release_id: str
    service_id: str
    version: str
    deployed_at: datetime | None = None
    source: str = "declared"
    created_at: datetime


class TelemetrySource(FrozenModel):
    source_id: str
    # kind: jsonl-file | otlp-json | otel-collector | prometheus | cloudwatch | api-gateway | other
    kind: str
    service_id: str
    enabled: bool = True
    config: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class Provenance(FrozenModel):
    source_id: str
    observed_at: datetime
    received_at: datetime
    adapter_version: str | None = None
    transform_notes: str | None = None


class ObservationBatch(FrozenModel):
    batch_id: str
    provenance: Provenance
    signal_types: list[SignalType] = Field(default_factory=list)
    quality: QualityState = QualityState.UNKNOWN
    item_count: int = 0
    idempotency_key: str | None = None


class EvidenceArtifact(FrozenModel):
    evidence_id: str
    kind: str  # metric-export | trace-extract | log-extract | document | receipt | other
    created_at: datetime
    hash: str  # sha256:<hex>
    size_bytes: int = 0
    truncated: bool = False
    loss_notes: str | None = None
    source_id: str | None = None
    blob_ref: str | None = None


class AuditEvent(FrozenModel):
    audit_id: str
    at: datetime
    actor: str
    action: str
    target_type: str
    target_id: str
    details: dict[str, str] = Field(default_factory=dict)


class IngestionCheckpoint(FrozenModel):
    source_id: str
    checkpoint: str  # opaque source-defined position
    updated_at: datetime
