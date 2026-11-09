"""Wire envelopes for the /v1 surface (docs/04).

Responses are typed models, never raw dicts. Every operational-state
response carries top-level `unknowns` (what could not be asserted) and
`evidence_refs` (pointers a client may resolve under authorization) —
unknowns never convert silently to defaults.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from api_sentinel.application.jobs import IngestionJob, Investigation
from api_sentinel.application.use_cases import (
    IncidentDetail,
    IncidentTimeline,
    OperationView,
    ServiceDetail,
    ServiceLinks,
    ServiceMetrics,
    ServiceSloPanel,
    ServiceSummary,
)
from api_sentinel.detection import Anomaly
from api_sentinel.domain import EvidenceArtifact
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.incidents import Incident


class ProblemDetails(FrozenModel):
    """RFC-7807 problem+json — typed Sentinel errors, never bare 500s.

    `code` is the machine-readable `sentinel.<name>` from the domain error;
    `details` carries the structured context the use case attached.
    """

    type: str
    title: str
    status: int
    detail: str
    code: str
    details: dict[str, Any] = Field(default_factory=dict)


class Envelope(FrozenModel):
    """The honest-read envelope — unknowns + evidence always present."""

    unknowns: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class CapabilityView(FrozenModel):
    capability_id: str
    status: str  # available | declared | unavailable — honest vocabulary
    stage: str = ""  # implemented|tested|integrated|verified|production_validated
    since: str | None = None
    notes: str = ""


class HealthResponse(Envelope):
    """`GET /v1/health` — the Sentinel's own operational state.

    `state` is `AVAILABLE` only when the workspace store is initialized and
    the database is present; a missing database is `PARTIAL` (the process
    serves, the data layer is uninitialized) — never a fabricated score.

    `sentinel` (Cycle 9, additive): live self-observability indicators —
    table counts, db size/integrity, observation span, source staleness,
    quarantine depth, checkpoint freshness — computed at read time from the
    store; an absent store yields the `unavailable` sub-state, not zeros.
    """

    state: str  # AVAILABLE | PARTIAL
    version: str
    workspace_initialized: bool
    database: str  # present | absent
    capabilities: dict[str, int] = Field(default_factory=dict)
    sentinel: dict[str, Any] = Field(default_factory=dict)


class CapabilitiesResponse(Envelope):
    capabilities: list[CapabilityView] = Field(default_factory=list)


class ServiceListResponse(Envelope):
    services: list[ServiceSummary] = Field(default_factory=list)


class ServiceDetailResponse(Envelope):
    detail: ServiceDetail
    links: ServiceLinks


class OperationListResponse(Envelope):
    operations: list[OperationView] = Field(default_factory=list)


class MetricsResponse(Envelope):
    metrics: ServiceMetrics


class SloPanelResponse(Envelope):
    panel: ServiceSloPanel


class IngestionJobRequest(FrozenModel):
    """`POST /v1/ingestion/jobs` body — an authorized import request.

    `file_ref` names a readable local path (canonicalized; UNC refused).
    `idempotency_key` dedups client retries; absent, a key is derived from
    source+file+content so a replay never imports twice.
    """

    source_id: str = Field(min_length=1)
    file_ref: str = Field(min_length=1)
    kind: str | None = None  # jsonl | otlp-json; auto-detected when absent
    idempotency_key: str | None = None


class JobResponse(Envelope):
    job: IngestionJob
    replayed: bool = False


class AnomalyListResponse(Envelope):
    anomalies: list[Anomaly] = Field(default_factory=list)
    truncated: bool = False


class IncidentListResponse(Envelope):
    incidents: list[Incident] = Field(default_factory=list)


class IncidentDetailResponse(Envelope):
    detail: IncidentDetail


class IncidentTimelineResponse(Envelope):
    timeline: IncidentTimeline


class InvestigationRequest(FrozenModel):
    """`POST /v1/incidents/{id}/investigations` body.

    `idempotency_key` dedups client retries into one investigation row;
    `note` is journaled on the incident (ACTION_RECORDED).
    """

    idempotency_key: str | None = None
    note: str | None = None


class InvestigationResponse(Envelope):
    investigation: Investigation
    replayed: bool = False


class EvidenceResponse(Envelope):
    """Metadata only — blob content is never served over the API."""

    evidence: EvidenceArtifact


class IntegrationStatusResponse(Envelope):
    integrations: list[dict[str, Any]] = Field(default_factory=list)
