"""Unit oracles for the incident lifecycle + append-only journal (docs/09).

The lifecycle is pure: it returns the next revision plus the journal entries
that justify it. Operational status and hypothesis confidence stay separate;
every mutation is journaled; CLOSED is terminal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api_sentinel.detection import (
    Anomaly,
    Explanation,
    RuleRef,
    Severity,
)
from api_sentinel.domain.errors import ValidationError
from api_sentinel.incidents import (
    TRANSITIONS,
    ConfidenceClass,
    EventKind,
    IncidentStatus,
    add_hypothesis,
    attach_anomaly,
    can_transition,
    group_key_for,
    incident_id_for,
    open_incident,
    record_note,
    set_hypothesis_confidence,
    transition,
)
from api_sentinel.metrics import TimeWindow

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
NOW = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
WIN = TimeWindow(start=T0, end=T0 + timedelta(minutes=10))


def _anomaly(
    *,
    anomaly_id: str = "anm_1",
    service_id: str = "svc_1",
    operation_id: str | None = "op_1",
    severity: Severity = Severity.SEV3,
    detector_id: str = "error_ratio_over_limit",
    rule_id: str = "rule_err",
    window_start: datetime = T0,
) -> Anomaly:
    return Anomaly(
        anomaly_id=anomaly_id,
        service_id=service_id,
        operation_id=operation_id,
        detected_at=NOW,
        window_start=window_start,
        window_end=window_start + timedelta(minutes=10),
        rule=RuleRef(
            rule_id=rule_id,
            rule_revision=1,
            detector_id=detector_id,
            detector_version="1",
        ),
        severity=severity,
        explanation=Explanation(summary=f"{detector_id} breached", math="x > y"),
        evidence_refs=["obs:o1"],
    )


def _open():
    return open_incident(anomaly=_anomaly(), service_name="payments-api", at=NOW)


# --- open + dedup -----------------------------------------------------------


def test_open_incident_is_deterministic() -> None:
    incident, events = _open()
    assert incident.status is IncidentStatus.DETECTED
    assert incident.incident_id == incident_id_for(
        group_key=group_key_for("svc_1", "op_1", "error_ratio_over_limit"),
        first_observed_at=T0,
    )
    (event,) = events
    assert event.kind is EventKind.OPENED
    assert event.from_status is None
    assert event.to_status is IncidentStatus.DETECTED
    assert "error_ratio_over_limit" in incident.title
    assert incident.dedup is not None
    assert incident.dedup.attached_anomalies == 1
    # Grouping justification is inspectable, not implicit.
    assert "detector_signature" in incident.dedup.grouped_by


def test_attach_same_anomaly_is_a_replay_noop() -> None:
    incident, _ = _open()
    updated, events = attach_anomaly(incident, _anomaly(), at=NOW)
    assert updated is incident or updated == incident
    assert events == []


def test_attach_second_anomaly_journals_the_grouping() -> None:
    incident, _ = _open()
    second = _anomaly(anomaly_id="anm_2", window_start=T0 + timedelta(minutes=10))
    updated, events = attach_anomaly(incident, second, at=NOW)
    assert updated.revision == incident.revision + 1
    assert updated.anomaly_ids == ["anm_1", "anm_2"]
    assert updated.dedup is not None
    assert updated.dedup.attached_anomalies == 2
    (event,) = events
    assert event.kind is EventKind.ANOMALY_ATTACHED
    assert "grouped_by" in event.details["justification"]


def test_worse_configured_severity_escalates_with_rule_revision() -> None:
    incident, _ = _open()
    worse = _anomaly(anomaly_id="anm_2", severity=Severity.SEV1)
    updated, events = attach_anomaly(incident, worse, at=NOW)
    assert updated.severity is Severity.SEV1
    kinds = [e.kind for e in events]
    assert kinds == [EventKind.ANOMALY_ATTACHED, EventKind.SEVERITY_ESCALATED]
    assert events[1].details["to_severity"] == "SEV1"
    assert events[1].details["rule_revision"] == "1"


def test_attach_to_closed_status_is_a_named_error() -> None:
    incident, _ = _open()
    resolved, _ = transition(incident, IncidentStatus.RESOLVED, at=NOW, actor="cli")
    closed, _ = transition(resolved, IncidentStatus.CLOSED, at=NOW, actor="cli")
    with pytest.raises(ValidationError, match="cannot attach"):
        attach_anomaly(closed, _anomaly(anomaly_id="anm_2"), at=NOW)


# --- state machine ------------------------------------------------------------


def test_transition_table_is_consistent() -> None:
    for status, allowed in TRANSITIONS.items():
        for target in allowed:
            assert can_transition(status, target)
        # every non-listed status is refused
        for target in IncidentStatus:
            if target not in allowed:
                assert not can_transition(status, target)
    assert can_transition(IncidentStatus.DETECTED, IncidentStatus.TRIAGED)
    assert not can_transition(IncidentStatus.DETECTED, IncidentStatus.CLOSED)


def test_ack_then_resolve_sets_timestamps() -> None:
    incident, _ = _open()
    acked, event = transition(incident, IncidentStatus.TRIAGED, at=NOW, actor="cli", note="looking")
    assert acked.status is IncidentStatus.TRIAGED
    assert acked.acknowledged_at == NOW
    assert event.kind is EventKind.STATUS_TRANSITION
    assert event.details == {} or event.details.get("note") is None

    resolved_at = NOW + timedelta(minutes=30)
    resolved, _ = transition(acked, IncidentStatus.RESOLVED, at=resolved_at, actor="cli")
    assert resolved.resolved_at == resolved_at
    assert resolved.revision == acked.revision + 1


def test_invalid_transition_is_a_named_error() -> None:
    incident, _ = _open()
    with pytest.raises(ValidationError, match="invalid incident transition"):
        transition(incident, IncidentStatus.CLOSED, at=NOW, actor="cli")


def test_resolved_reopens_only_via_journal() -> None:
    incident, _ = _open()
    resolved, _ = transition(incident, IncidentStatus.RESOLVED, at=NOW, actor="cli")
    reopened, event = transition(
        resolved, IncidentStatus.REOPENED, at=NOW + timedelta(minutes=5), actor="cli"
    )
    assert reopened.status is IncidentStatus.REOPENED
    assert reopened.resolved_at is None
    assert event.kind is EventKind.STATUS_TRANSITION
    assert event.from_status is IncidentStatus.RESOLVED
    assert event.to_status is IncidentStatus.REOPENED
    # Re-entering the active flow is allowed again.
    assert can_transition(IncidentStatus.REOPENED, IncidentStatus.TRIAGED)


def test_dismissed_can_reopen_but_closed_is_terminal() -> None:
    incident, _ = _open()
    dismissed, _ = transition(incident, IncidentStatus.DISMISSED, at=NOW, actor="cli")
    assert dismissed.status is IncidentStatus.DISMISSED
    assert can_transition(IncidentStatus.DISMISSED, IncidentStatus.REOPENED)
    resolved, _ = transition(incident, IncidentStatus.RESOLVED, at=NOW, actor="cli")
    closed, _ = transition(resolved, IncidentStatus.CLOSED, at=NOW, actor="cli")
    assert TRANSITIONS[IncidentStatus.CLOSED] == frozenset()
    with pytest.raises(ValidationError, match="invalid incident transition"):
        transition(closed, IncidentStatus.REOPENED, at=NOW, actor="cli")


def test_closed_incident_still_takes_journaled_notes() -> None:
    incident, _ = _open()
    resolved, _ = transition(incident, IncidentStatus.RESOLVED, at=NOW, actor="cli")
    closed, _ = transition(resolved, IncidentStatus.CLOSED, at=NOW, actor="cli")
    event = record_note(closed, at=NOW, actor="cli", note="post-mortem link added")
    # Same-status append: the closed row is untouched, the journal grows.
    assert event.from_status is IncidentStatus.CLOSED
    assert event.to_status is IncidentStatus.CLOSED
    assert event.kind is EventKind.NOTE


# --- hypotheses: confidence separate from workflow ----------------------------


def test_hypothesis_confidence_is_independent_of_status() -> None:
    incident, _ = _open()
    updated, event = add_hypothesis(
        incident,
        hypothesis_id="hyp_1",
        text="deploy r12 correlated with latency rise",
        confidence=ConfidenceClass.HYPOTHESIZED,
        evidence_refs=["obs:o1"],
        at=NOW,
        actor="cli",
    )
    assert updated.status is IncidentStatus.DETECTED  # status untouched
    assert updated.hypotheses[0].confidence is ConfidenceClass.HYPOTHESIZED
    assert event.kind is EventKind.HYPOTHESIS_ADDED


def test_verified_hypothesis_requires_independent_evidence() -> None:
    incident, _ = _open()
    with pytest.raises(ValidationError, match="VERIFIED hypothesis requires"):
        add_hypothesis(
            incident,
            hypothesis_id="hyp_1",
            text="root cause: bad deploy",
            confidence=ConfidenceClass.VERIFIED,
            evidence_refs=[],
            at=NOW,
            actor="cli",
        )
    updated, _ = add_hypothesis(
        incident,
        hypothesis_id="hyp_2",
        text="deploy r12 confirmed by rollback",
        confidence=ConfidenceClass.VERIFIED,
        evidence_refs=["runbook:rb-7", "deploy:d12"],
        at=NOW,
        actor="cli",
    )
    assert updated.hypotheses[0].confidence is ConfidenceClass.VERIFIED


def test_hypothesis_confidence_updates_are_journaled() -> None:
    incident, _ = _open()
    with_hyp, _ = add_hypothesis(
        incident,
        hypothesis_id="hyp_1",
        text="db saturation",
        confidence=ConfidenceClass.OBSERVED,
        evidence_refs=["obs:o1"],
        at=NOW,
        actor="cli",
    )
    updated, event = set_hypothesis_confidence(
        with_hyp,
        "hyp_1",
        confidence=ConfidenceClass.CORRELATED,
        evidence_refs=["obs:o1", "dep:db_p95"],
        at=NOW,
        actor="cli",
    )
    assert updated.hypotheses[0].confidence is ConfidenceClass.CORRELATED
    assert event.details["from_confidence"] == "OBSERVED"
    assert event.details["to_confidence"] == "CORRELATED"
    with pytest.raises(ValidationError, match="hypothesis not found"):
        set_hypothesis_confidence(
            updated,
            "hyp_ghost",
            confidence=ConfidenceClass.OBSERVED,
            evidence_refs=[],
            at=NOW,
            actor="cli",
        )


# --- identity -----------------------------------------------------------------


def test_group_key_covers_service_operation_and_detector() -> None:
    key = group_key_for("svc_1", "op_1", "error_ratio_over_limit")
    assert key == "svc_1|op_1|error_ratio_over_limit"
    assert group_key_for("svc_1", None, "k") == "svc_1|service|k"
    # Different operation scopes never collide into one incident.
    assert group_key_for("svc_1", "op_2", "k") != key
