"""Core domain entities (Cycle 1 subset) — frozen, dependency-free."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

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


class ChangeKind(StrEnum):
    """Change classes correlated during investigation (docs/03, Cycle 5/8)."""

    COMMIT = "commit"
    RELEASE = "release"
    DEPLOY = "deploy"
    ROLLBACK = "rollback"
    CONFIG = "config"
    FLAG = "flag"
    GATEWAY = "gateway"  # gateway/routing/ingress changes (Cycle 8)
    AUTH_POLICY = "auth_policy"  # authn/authz policy changes (Cycle 8)
    CONTRACT = "contract"
    DEPENDENCY = "dependency"
    MAINTENANCE = "maintenance"


class ChangeEvent(FrozenModel):
    """A deploy/config/flag/contract/dependency/maintenance change.

    `at` is event time (when the change happened); `created_at` is when the
    Sentinel recorded it — the two clocks are never conflated. `ref` is an
    opaque external pointer (release id, commit sha, ticket, flag name).
    """

    change_id: str
    service_id: str
    kind: ChangeKind
    at: datetime
    source: str  # declared | cli | import:<file> | webhook — provenance
    ref: str | None = None
    details: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


class DependencyObservation(FrozenModel):
    """An *observed* inter-service edge — the operational graph (docs/03).

    Sentinel-owned: an edge means a call/failure/latency was *observed*
    between `service_id` and `target` during a window — never a claim that
    a static code path exists (that is Atlas's structural graph; the two
    stay conceptually distinct, docs/06). `edge_id` is deterministic over
    (service, target, operation, window) so reprocessing is idempotent.
    """

    edge_id: str
    service_id: str  # the observed caller (canonical Sentinel identity)
    target: str  # the observed callee — a service_id or an external label
    target_kind: str = "service"  # service | datastore | external
    operation_id: str | None = None
    window_start: datetime
    window_end: datetime
    calls: int = 0
    errors: int = 0
    timeouts: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: str = "observed"  # observed | inferred — never "declared"
    created_at: datetime


class HandoffStatus(StrEnum):
    """Handoff lifecycle — every terminal state is named, never a hang."""

    CREATED = "created"  # envelope built; delegation not yet attempted
    DELEGATED = "delegated"  # emitted to the target transport (file/in-process)
    COMPLETED = "completed"  # findings ingested back as evidence
    REFUSED = "refused"  # named refusal (unknown version, missing scope)
    TIMED_OUT = "timed_out"  # response budget exceeded — terminal
    UNAVAILABLE = "unavailable"  # target absent — never simulated
    FAILED = "failed"  # transport/parse failure, reason recorded


class ExternalHandoff(FrozenModel):
    """A governed delegation to a specialist Forge (docs/03).

    `request_ref`/`response_ref` point at the persisted envelopes (handoff
    file path, findings bundle path) — never payloads; the receipt artifact
    under `receipts/` carries the redacted run summary.
    """

    handoff_id: str
    to_forge: str
    kind: str
    status: HandoffStatus
    request_ref: str | None = None
    response_ref: str | None = None
    detail: str = ""  # named-refusal/failure reason when one occurred
    created_at: datetime
