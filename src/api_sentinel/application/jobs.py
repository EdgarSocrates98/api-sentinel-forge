"""Ops records persisted by the local HTTP surface (Cycle 6).

`Investigation` — one controlled investigation launched over the API: the
row is the audit record; `context_pack` is the bounded assembled pack
(incident + anomalies + evidence refs + latest indicators + structural
context when the Atlas adapter has data — `UNAVAILABLE`, never fabricated).

`IngestionJob` — one authorized import initiated over HTTP. Execution is
bounded-synchronous; every transition is recorded so the job resource stays
auditable. The `idempotency_key` (client-supplied or derived from
source+file+content) makes re-posts return the same job, never a duplicate.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from api_sentinel.detection.protocol import Anomaly
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.models import EvidenceArtifact
from api_sentinel.incidents.journal import IncidentEvent
from api_sentinel.incidents.models import Incident
from api_sentinel.integrations.atlas.models import StructuralEdge, StructuralRef
from api_sentinel.integrations.correlate import CorrelationReport
from api_sentinel.reliability.health import ServiceHealthReport


class InvestigationStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StructuralContextView(FrozenModel):
    """The Atlas structural slice of a pack — a named state, never a guess.

    `AVAILABLE` = the service's declared `code_atlas_identity` resolved to a
    structural entity and edges were read. `UNRESOLVED` = the adapter is wired
    but the canonical identity carries no resolvable Atlas ref (ADR-012 — no
    name-matched guess). `UNAVAILABLE` = no adapter data at all.
    """

    state: str  # AVAILABLE | UNRESOLVED | UNAVAILABLE
    reason: str = ""
    atlas_ref: StructuralRef | None = None
    dependencies: list[StructuralEdge] = Field(default_factory=list)
    dependents: list[StructuralEdge] = Field(default_factory=list)


class InvestigationPack(FrozenModel):
    """The bounded context pack persisted on the investigation row."""

    incident: Incident
    events: list[IncidentEvent] = Field(default_factory=list)
    anomalies: list[Anomaly] = Field(default_factory=list)
    evidence: list[EvidenceArtifact] = Field(default_factory=list)
    evidence_unresolved: list[str] = Field(default_factory=list)
    indicators: ServiceHealthReport | None = None
    correlation: CorrelationReport | None = None
    structural_context: StructuralContextView
    truncated: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class Investigation(FrozenModel):
    """Audit row for one controlled investigation (migration 0006)."""

    investigation_id: str
    incident_id: str
    status: InvestigationStatus
    result: dict[str, Any] = Field(default_factory=dict)
    context_pack: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class IngestionJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class IngestionJob(FrozenModel):
    """Audit row for one HTTP-initiated import (migration 0006)."""

    job_id: str
    source_id: str
    file_ref: str
    kind: str | None = None
    status: IngestionJobStatus = IngestionJobStatus.QUEUED
    accepted: int = 0
    quarantined: int = 0
    skipped_duplicates: int = 0
    batch_id: str | None = None
    error: str | None = None
    idempotency_key: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
