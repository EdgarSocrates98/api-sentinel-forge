"""Incident lifecycle — the operational state machine (docs/09).

Pure domain: every function takes the current `Incident` plus injected time
and returns the next revision plus the journal entries that justify it.
Nothing is persisted here and nothing mutates silently — `revision` bumps on
every change so writers enforce concurrency control, and a CLOSED incident
accepts no transition (journal notes only — invariant).
"""

from __future__ import annotations

from datetime import datetime

from api_sentinel.detection.protocol import SEVERITY_RANK, Anomaly, RuleRef
from api_sentinel.domain.errors import ValidationError
from api_sentinel.incidents.journal import EventKind, IncidentEvent, make_event
from api_sentinel.incidents.models import (
    OPEN_STATUSES,
    TRANSITIONS,
    ConfidenceClass,
    DedupInfo,
    Hypothesis,
    Incident,
    IncidentAction,
    IncidentStatus,
    group_key_for,
    incident_id_for,
)

GROUPED_BY = [
    "service_id",
    "operation_scope",
    "detector_signature",
    "unresolved_status",
    "shared_environment",
]


def can_transition(from_status: IncidentStatus, to_status: IncidentStatus) -> bool:
    return to_status in TRANSITIONS[from_status]


def _bump(incident: Incident, **changes: object) -> Incident:
    return incident.model_copy(update={**changes, "revision": incident.revision + 1})


def open_incident(
    *,
    anomaly: Anomaly,
    service_name: str,
    at: datetime,
    actor: str = "system",
) -> tuple[Incident, list[IncidentEvent]]:
    """Open a DETECTED incident for the anomaly's group signature."""
    group_key = group_key_for(
        anomaly.service_id, anomaly.operation_id, anomaly.rule.detector_id
    )
    incident_id = incident_id_for(
        group_key=group_key, first_observed_at=anomaly.window_start
    )
    title = f"{anomaly.rule.detector_id} on {service_name}"
    if anomaly.operation_ref:
        title += f" ({anomaly.operation_ref})"
    rule_ref = anomaly.rule
    incident = Incident(
        incident_id=incident_id,
        title=title,
        severity=anomaly.severity,
        status=IncidentStatus.DETECTED,
        service_id=anomaly.service_id,
        operation_id=anomaly.operation_id,
        operation_ref=anomaly.operation_ref,
        group_key=group_key,
        first_observed_at=anomaly.window_start,
        detected_at=at,
        revision=1,
        service_refs=[anomaly.service_id],
        operation_refs=[anomaly.operation_id] if anomaly.operation_id else [],
        rule_refs=[rule_ref],
        anomaly_ids=[anomaly.anomaly_id],
        evidence_refs=list(anomaly.evidence_refs),
        limitations=[f"anomaly_unknowns:{u}" for u in anomaly.unknowns],
        dedup=DedupInfo(
            group_key=group_key,
            grouped_by=GROUPED_BY,
            attached_anomalies=1,
        ),
    )
    event = make_event(
        incident_id=incident_id,
        kind=EventKind.OPENED,
        at=at,
        actor=actor,
        from_status=None,
        to_status=IncidentStatus.DETECTED,
        note=anomaly.explanation.summary,
        details={
            "anomaly_id": anomaly.anomaly_id,
            "rule_id": rule_ref.rule_id,
            "rule_revision": str(rule_ref.rule_revision),
            "detector_id": rule_ref.detector_id,
            "detector_version": rule_ref.detector_version,
            "grouped_by": ",".join(GROUPED_BY),
        },
    )
    return incident, [event]


def attach_anomaly(
    incident: Incident,
    anomaly: Anomaly,
    *,
    at: datetime,
    actor: str = "system",
) -> tuple[Incident, list[IncidentEvent]]:
    """Attach an anomaly to an open incident — the dedup path (docs/09).

    The original anomaly is preserved (it keeps its own row); the journal
    records *why* it grouped here. A worse configured severity escalates the
    incident and the escalation is journaled with the rule revision that
    fired it — escalation is defined by versioned rules.
    """
    if incident.status not in OPEN_STATUSES:
        raise ValidationError(
            f"cannot attach anomaly to incident in status {incident.status.value}",
            details={"incident_id": incident.incident_id},
        )
    if anomaly.anomaly_id in incident.anomaly_ids:
        return incident, []  # already attached — replay is a no-op
    dedup = incident.dedup or DedupInfo(
        group_key=incident.group_key, grouped_by=GROUPED_BY
    )
    changes: dict[str, object] = {
        "anomaly_ids": [*incident.anomaly_ids, anomaly.anomaly_id],
        "evidence_refs": sorted(
            set(incident.evidence_refs) | set(anomaly.evidence_refs)
        ),
        "rule_refs": _merge_rule_refs(incident.rule_refs, anomaly.rule),
        "first_observed_at": min(incident.first_observed_at, anomaly.window_start),
        "dedup": dedup.model_copy(
            update={"attached_anomalies": dedup.attached_anomalies + 1}
        ),
    }
    escalated = SEVERITY_RANK[anomaly.severity] > SEVERITY_RANK[incident.severity]
    if escalated:
        changes["severity"] = anomaly.severity
    updated = _bump(incident, **changes)
    events = [
        make_event(
            incident_id=incident.incident_id,
            kind=EventKind.ANOMALY_ATTACHED,
            at=at,
            actor=actor,
            from_status=incident.status,
            to_status=incident.status,
            note=anomaly.explanation.summary,
            details={
                "anomaly_id": anomaly.anomaly_id,
                "rule_id": anomaly.rule.rule_id,
                "rule_revision": str(anomaly.rule.rule_revision),
                "justification": "same service+operation scope, detector "
                "signature and unresolved status (grouped_by: "
                + ",".join(GROUPED_BY)
                + ")",
            },
            discriminator=anomaly.anomaly_id,
        )
    ]
    if escalated:
        events.append(
            make_event(
                incident_id=incident.incident_id,
                kind=EventKind.SEVERITY_ESCALATED,
                at=at,
                actor=actor,
                from_status=incident.status,
                to_status=incident.status,
                note=(
                    f"severity {incident.severity.value} → "
                    f"{anomaly.severity.value} via "
                    f"{anomaly.rule.rule_id}@{anomaly.rule.rule_revision}"
                ),
                details={
                    "from_severity": incident.severity.value,
                    "to_severity": anomaly.severity.value,
                    "rule_id": anomaly.rule.rule_id,
                    "rule_revision": str(anomaly.rule.rule_revision),
                },
                discriminator=f"{anomaly.anomaly_id}|severity",
            )
        )
    return updated, events


def transition(
    incident: Incident,
    to_status: IncidentStatus,
    *,
    at: datetime,
    actor: str,
    note: str | None = None,
    details: dict[str, str] | None = None,
) -> tuple[Incident, IncidentEvent]:
    """One journaled workflow transition; invalid moves are named errors.

    CLOSED is terminal — a closed incident is never edited, only appended
    (notes/hypotheses keep working; its status never moves again).
    """
    if not can_transition(incident.status, to_status):
        raise ValidationError(
            f"invalid incident transition: "
            f"{incident.status.value} → {to_status.value}",
            details={
                "incident_id": incident.incident_id,
                "allowed": sorted(s.value for s in TRANSITIONS[incident.status]),
            },
        )
    changes: dict[str, object] = {"status": to_status}
    if to_status in (IncidentStatus.TRIAGED, IncidentStatus.INVESTIGATING):
        if incident.acknowledged_at is None:
            changes["acknowledged_at"] = at
    elif to_status is IncidentStatus.RESOLVED:
        changes["resolved_at"] = at
    elif to_status is IncidentStatus.REOPENED:
        # Reopen only via this journaled event; the journal keeps the prior
        # resolved_at so the current-state row reflects "not resolved".
        changes["resolved_at"] = None
        if incident.acknowledged_at is None:
            changes["acknowledged_at"] = at
    updated = _bump(incident, **changes)
    event = make_event(
        incident_id=incident.incident_id,
        kind=EventKind.STATUS_TRANSITION,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=to_status,
        note=note,
        details=details,
        discriminator=f"{incident.status.value}>{to_status.value}|{note or ''}",
    )
    return updated, event


def record_note(
    incident: Incident,
    *,
    at: datetime,
    actor: str,
    kind: EventKind = EventKind.NOTE,
    note: str,
    details: dict[str, str] | None = None,
    discriminator: str | None = None,
) -> IncidentEvent:
    """Append-only entry that does not move the status — the only mutation a
    CLOSED incident can take (same-status journal append)."""
    return make_event(
        incident_id=incident.incident_id,
        kind=kind,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=note,
        details=details,
        discriminator=discriminator,
    )


def recovery_observed(
    incident: Incident,
    *,
    at: datetime,
    actor: str = "system",
    rule_ref: RuleRef,
    window_start: datetime,
    window_end: datetime,
) -> IncidentEvent:
    """Journal that the condition behind this incident no longer holds.

    Honest detection of recovery: the grouping rule evaluated clean over the
    window — recorded as evidence; a human (or a later policy) still resolves.
    """
    return record_note(
        incident,
        at=at,
        actor=actor,
        kind=EventKind.RECOVERY_OBSERVED,
        note=(
            f"{rule_ref.detector_id} evaluated clean over "
            f"{window_start.isoformat()}..{window_end.isoformat()} — "
            "breach no longer observed"
        ),
        details={
            "rule_id": rule_ref.rule_id,
            "rule_revision": str(rule_ref.rule_revision),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
        discriminator=f"recovery|{rule_ref.rule_id}|{window_end.isoformat()}",
    )


def add_hypothesis(
    incident: Incident,
    *,
    hypothesis_id: str,
    text: str,
    confidence: ConfidenceClass,
    evidence_refs: list[str],
    at: datetime,
    actor: str,
    note: str | None = None,
) -> tuple[Incident, IncidentEvent]:
    """Attach a candidate cause — confidence class, never workflow state.

    `VERIFIED` requires independent evidence (docs/09): an empty
    `evidence_refs` at VERIFIED is a named error, not silent acceptance.
    """
    if confidence is ConfidenceClass.VERIFIED and not evidence_refs:
        raise ValidationError(
            "VERIFIED hypothesis requires independent evidence_refs",
            details={"incident_id": incident.incident_id},
        )
    hypothesis = Hypothesis(
        hypothesis_id=hypothesis_id,
        text=text,
        confidence=confidence,
        evidence_refs=list(evidence_refs),
        note=note,
        created_at=at,
        updated_at=at,
    )
    updated = _bump(incident, hypotheses=[*incident.hypotheses, hypothesis])
    event = make_event(
        incident_id=incident.incident_id,
        kind=EventKind.HYPOTHESIS_ADDED,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=text,
        details={
            "hypothesis_id": hypothesis_id,
            "confidence": confidence.value,
        },
        discriminator=hypothesis_id,
    )
    return updated, event


def set_hypothesis_confidence(
    incident: Incident,
    hypothesis_id: str,
    *,
    confidence: ConfidenceClass,
    evidence_refs: list[str],
    at: datetime,
    actor: str,
    note: str | None = None,
) -> tuple[Incident, IncidentEvent]:
    """Move a hypothesis along the confidence ladder; journaled."""
    found = next(
        (h for h in incident.hypotheses if h.hypothesis_id == hypothesis_id), None
    )
    if found is None:
        raise ValidationError(
            f"hypothesis not found: {hypothesis_id}",
            details={"incident_id": incident.incident_id},
        )
    if confidence is ConfidenceClass.VERIFIED and not evidence_refs:
        raise ValidationError(
            "VERIFIED hypothesis requires independent evidence_refs",
            details={"incident_id": incident.incident_id},
        )
    updated_hyp = found.model_copy(
        update={
            "confidence": confidence,
            "evidence_refs": sorted(set(evidence_refs)),
            "note": note or found.note,
            "updated_at": at,
        }
    )
    hypotheses = [
        updated_hyp if h.hypothesis_id == hypothesis_id else h
        for h in incident.hypotheses
    ]
    updated = _bump(incident, hypotheses=hypotheses)
    event = make_event(
        incident_id=incident.incident_id,
        kind=EventKind.HYPOTHESIS_UPDATED,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=note or updated_hyp.text,
        details={
            "hypothesis_id": hypothesis_id,
            "from_confidence": found.confidence.value,
            "to_confidence": confidence.value,
        },
        discriminator=f"{hypothesis_id}|{found.confidence.value}>{confidence.value}",
    )
    return updated, event


def record_action(
    incident: Incident,
    *,
    kind: str,
    note: str,
    at: datetime,
    actor: str,
) -> tuple[Incident, IncidentEvent]:
    """Record an executed check/action (e.g. 'dependency trace compared')."""
    action = IncidentAction(at=at, actor=actor, kind=kind, note=note)
    updated = _bump(incident, actions=[*incident.actions, action])
    event = make_event(
        incident_id=incident.incident_id,
        kind=EventKind.ACTION_RECORDED,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=note,
        details={"action_kind": kind},
        discriminator=f"{kind}|{note}",
    )
    return updated, event


def assign_owner(
    incident: Incident,
    *,
    owner: str,
    at: datetime,
    actor: str,
) -> tuple[Incident, IncidentEvent]:
    updated = _bump(incident, owner=owner)
    event = record_note(
        updated,
        at=at,
        actor=actor,
        note=f"owner assigned: {owner}",
        details={"owner": owner},
        discriminator=f"owner|{owner}",
    )
    return updated, event


def _merge_rule_refs(existing: list[RuleRef], new: RuleRef) -> list[RuleRef]:
    seen = {
        (r.rule_id, r.rule_revision, r.detector_id, r.detector_version)
        for r in existing
    }
    key = (new.rule_id, new.rule_revision, new.detector_id, new.detector_version)
    return existing if key in seen else [*existing, new]
