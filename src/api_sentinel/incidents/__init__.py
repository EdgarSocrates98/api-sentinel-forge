"""Incidents bounded context — lifecycle + append-only journal (ADR-008).

Domain-pure: functions take the current row plus injected time and return
the next revision plus journal entries; storage is the caller's job. The
journal is the source of truth for transitions — a closed incident is
modified only through new journal entries (invariant).
"""

from api_sentinel.incidents.journal import (
    EventKind,
    IncidentEvent,
    event_id_for,
    make_event,
)
from api_sentinel.incidents.lifecycle import (
    add_hypothesis,
    assign_owner,
    attach_anomaly,
    can_transition,
    open_incident,
    record_action,
    record_note,
    recovery_observed,
    set_hypothesis_confidence,
    transition,
)
from api_sentinel.incidents.models import (
    OPEN_STATUSES,
    TRANSITIONS,
    ConfidenceClass,
    DedupInfo,
    Hypothesis,
    Incident,
    IncidentAction,
    IncidentStatus,
    VerificationRecord,
    group_key_for,
    incident_id_for,
)

__all__ = [
    "OPEN_STATUSES",
    "TRANSITIONS",
    "ConfidenceClass",
    "DedupInfo",
    "EventKind",
    "Hypothesis",
    "Incident",
    "IncidentAction",
    "IncidentEvent",
    "IncidentStatus",
    "VerificationRecord",
    "add_hypothesis",
    "assign_owner",
    "attach_anomaly",
    "can_transition",
    "event_id_for",
    "group_key_for",
    "incident_id_for",
    "make_event",
    "open_incident",
    "record_action",
    "record_note",
    "recovery_observed",
    "set_hypothesis_confidence",
    "transition",
]
