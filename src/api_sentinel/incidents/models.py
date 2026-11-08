"""Incident entity — operational investigation object (docs/09, ADR-008).

An incident carries an *operational status* (workflow state machine) and an
independent *confidence* dimension carried by hypotheses — never conflated:
`INVESTIGATING` with a `HYPOTHESIZED` cause does not imply a proven cause.
Severity is impact-driven (SEV1..SEV4 + INFORMATIONAL), configured per
service through rule policy — never derived from HTTP status codes.

Current-state row + journal storage (ADR-008): the row answers queries
fast; `incident_events` preserves every transition with provenance. A closed
incident is modified only through new journal entries (invariant).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from api_sentinel.detection.protocol import RuleRef, Severity
from api_sentinel.domain.identity import FrozenModel


class IncidentStatus(StrEnum):
    """Workflow states (docs/09 status ladder)."""

    DETECTED = "DETECTED"  # anomaly grouped here; awaiting triage
    TRIAGED = "TRIAGED"  # acknowledged / scoped
    INVESTIGATING = "INVESTIGATING"  # active analysis
    MITIGATING = "MITIGATING"  # mitigation in progress
    MONITORING = "MONITORING"  # fix applied; watching for recovery
    RESOLVED = "RESOLVED"  # observed recovery; reopenable
    REOPENED = "REOPENED"  # was resolved/dismissed; reopened via journal
    DISMISSED = "DISMISSED"  # closed as not-an-incident; reopenable
    CLOSED = "CLOSED"  # terminal; mutates only via new journal entries


#: Statuses that keep an incident on the dedup grouping radar — a new anomaly
#: for the same signature attaches here instead of spawning a duplicate.
OPEN_STATUSES: frozenset[IncidentStatus] = frozenset(
    {
        IncidentStatus.DETECTED,
        IncidentStatus.TRIAGED,
        IncidentStatus.INVESTIGATING,
        IncidentStatus.MITIGATING,
        IncidentStatus.MONITORING,
        IncidentStatus.REOPENED,
    }
)

#: Transitions of the operational state machine. REOPENED re-enters the
#: active flow; CLOSED is terminal (mutation only via journal events).
TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.DETECTED: frozenset(
        {
            IncidentStatus.TRIAGED,
            IncidentStatus.INVESTIGATING,
            IncidentStatus.RESOLVED,
            IncidentStatus.DISMISSED,
        }
    ),
    IncidentStatus.TRIAGED: frozenset(
        {
            IncidentStatus.INVESTIGATING,
            IncidentStatus.RESOLVED,
            IncidentStatus.DISMISSED,
        }
    ),
    IncidentStatus.INVESTIGATING: frozenset(
        {
            IncidentStatus.MITIGATING,
            IncidentStatus.MONITORING,
            IncidentStatus.RESOLVED,
            IncidentStatus.DISMISSED,
        }
    ),
    IncidentStatus.MITIGATING: frozenset(
        {
            IncidentStatus.MONITORING,
            IncidentStatus.RESOLVED,
            IncidentStatus.INVESTIGATING,
        }
    ),
    IncidentStatus.MONITORING: frozenset(
        {IncidentStatus.RESOLVED, IncidentStatus.INVESTIGATING}
    ),
    IncidentStatus.RESOLVED: frozenset(
        {IncidentStatus.REOPENED, IncidentStatus.CLOSED}
    ),
    IncidentStatus.REOPENED: frozenset(
        {IncidentStatus.TRIAGED, IncidentStatus.INVESTIGATING}
    ),
    IncidentStatus.DISMISSED: frozenset({IncidentStatus.REOPENED}),
    IncidentStatus.CLOSED: frozenset(),
}


class ConfidenceClass(StrEnum):
    """Hypothesis confidence ladder — separate from operational status.

    `OBSERVED → CORRELATED → HYPOTHESIZED → VERIFIED`; VERIFIED requires
    independent tool/procedure evidence (docs/09). No fake precision: states
    and observable evidence, never invented percentages.
    """

    OBSERVED = "OBSERVED"
    CORRELATED = "CORRELATED"
    HYPOTHESIZED = "HYPOTHESIZED"
    VERIFIED = "VERIFIED"


class Hypothesis(FrozenModel):
    """A candidate cause with explicit, explainable confidence."""

    hypothesis_id: str
    text: str
    confidence: ConfidenceClass = ConfidenceClass.OBSERVED
    evidence_refs: list[str] = Field(default_factory=list)
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class IncidentAction(FrozenModel):
    """An executed check/action recorded on the incident."""

    at: datetime
    actor: str
    kind: str
    note: str


class DedupInfo(FrozenModel):
    """Why anomalies group into this incident — the recorded justification."""

    group_key: str
    grouped_by: list[str] = Field(default_factory=list)
    attached_anomalies: int = 0


class Incident(FrozenModel):
    """Current-state incident row (ADR-008); history lives in the journal."""

    incident_id: str
    title: str
    severity: Severity
    status: IncidentStatus
    service_id: str
    operation_id: str | None = None
    operation_ref: str | None = None
    group_key: str  # dedup signature — see `group_key_for`
    first_observed_at: datetime
    detected_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    owner: str | None = None
    revision: int = 1  # concurrency control: writers must expect this value
    service_refs: list[str] = Field(default_factory=list)
    operation_refs: list[str] = Field(default_factory=list)
    rule_refs: list[RuleRef] = Field(default_factory=list)
    anomaly_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    change_refs: list[str] = Field(default_factory=list)
    actions: list[IncidentAction] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    dedup: DedupInfo | None = None


def group_key_for(service_id: str, operation_id: str | None, kind: str) -> str:
    """Dedup signature: same (service, operation scope, rule kind, unresolved)
    attaches to one incident — never a duplicate (docs/09).

    Multi-key match documented: the primary keys are service_id + operation
    scope + detector signature + unresolved status; environment, release and
    shared dependency are Level-1-constant (single workspace environment;
    release is carried as anomaly evidence, not a separate grouping entity).
    """
    return f"{service_id}|{operation_id or 'service'}|{kind}"


def incident_id_for(
    *, group_key: str, first_observed_at: datetime
) -> str:
    """Deterministic incident identity — replay reproduces the same id."""
    canonical = f"{group_key}|{first_observed_at.isoformat()}"
    return "inc_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
