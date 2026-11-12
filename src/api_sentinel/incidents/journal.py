"""Incident journal — append-only event entries (docs/09, ADR-008).

Every state change on an incident appends an `incident_events` row: opens,
transitions, anomaly attaches, severity escalations, hypotheses, notes,
recovery observations, reopens. The journal is the source of truth for the
timeline; the current-state row is a fast read model.

The repository exposes no update/delete path — the journal can only grow.
Event ids are deterministic (content hash) so a replayed detection run is a
no-op, not a duplicate entry.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.incidents.models import IncidentStatus


class EventKind(StrEnum):
    """What sort of state change a journal entry records."""

    OPENED = "opened"
    STATUS_TRANSITION = "status_transition"
    ANOMALY_ATTACHED = "anomaly_attached"
    SEVERITY_ESCALATED = "severity_escalated"
    HYPOTHESIS_ADDED = "hypothesis_added"
    HYPOTHESIS_UPDATED = "hypothesis_updated"
    ACTION_RECORDED = "action_recorded"
    RECOVERY_OBSERVED = "recovery_observed"
    VERIFICATION_RECORDED = "verification_recorded"
    SESSION_REQUESTED = "session_requested"
    NOTE = "note"


class IncidentEvent(FrozenModel):
    """One append-only journal entry — never updated, never deleted."""

    event_id: str
    incident_id: str
    at: datetime
    actor: str  # 'system' (detector/dedup) | 'cli' | user identity
    kind: EventKind
    from_status: IncidentStatus | None = None
    to_status: IncidentStatus | None = None
    note: str | None = None
    details: dict[str, str] = Field(default_factory=dict)


def event_id_for(
    *,
    incident_id: str,
    kind: EventKind,
    at: datetime,
    discriminator: str,
) -> str:
    """Deterministic event identity — replays dedup by primary key."""
    canonical = f"{incident_id}|{kind.value}|{at.isoformat()}|{discriminator}"
    return "iev_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def make_event(
    *,
    incident_id: str,
    kind: EventKind,
    at: datetime,
    actor: str,
    from_status: IncidentStatus | None = None,
    to_status: IncidentStatus | None = None,
    note: str | None = None,
    details: dict[str, str] | None = None,
    discriminator: str | None = None,
) -> IncidentEvent:
    """Build a journal entry with a deterministic id."""
    detail = details or {}
    disc = discriminator or "|".join(f"{k}={v}" for k, v in sorted(detail.items())) or (note or "")
    return IncidentEvent(
        event_id=event_id_for(incident_id=incident_id, kind=kind, at=at, discriminator=disc),
        incident_id=incident_id,
        at=at,
        actor=actor,
        kind=kind,
        from_status=from_status,
        to_status=to_status,
        note=note,
        details=detail,
    )
