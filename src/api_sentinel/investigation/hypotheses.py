"""Hypothesis lifecycle on incidents (Cycle 8, docs/09).

A hypothesis is created at `CORRELATED` or `HYPOTHESIZED` — never directly
at `VERIFIED` — carrying explicit `evidence_for` / `evidence_against` /
`unknowns` (explainable states, no fabricated numeric confidence).

- **Promote** to `VERIFIED` only when a verification produced independent
  evidence: the `verification_id` must name a persisted
  `VerificationRecord` on the incident whose verdict is `VERIFIED` and
  whose `hypothesis_id` matches — promotion is never self-declared.
- **Demote** to `REFUTED` — the terminal contradiction state — always
  with a recorded reason.

Every transition appends a journal event; a CLOSED incident still accepts
hypothesis appends (journal-only mutation, invariant).
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.domain.errors import NotFoundError, ValidationError
from api_sentinel.domain.ids import IdFactory
from api_sentinel.incidents.journal import EventKind, IncidentEvent, make_event
from api_sentinel.incidents.models import (
    ConfidenceClass,
    Hypothesis,
    Incident,
    VerificationRecord,
)

#: Confidence classes a hypothesis may be *created* with — VERIFIED and
#: REFUTED are outcomes of evidence, never starting points; OBSERVED is a
#: raw signal, not yet a candidate cause.
CREATABLE_CONFIDENCE = frozenset({ConfidenceClass.CORRELATED, ConfidenceClass.HYPOTHESIZED})


def hypothesis_id_for(*, incident_id: str, text: str, at: datetime) -> str:
    """Deterministic hypothesis identity — replay dedups by id."""
    canonical = f"{incident_id}|{text}|{at.isoformat()}"
    return "hyp_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _bump(incident: Incident, **changes: object) -> Incident:
    return incident.model_copy(update={**changes, "revision": incident.revision + 1})


def create_hypothesis(
    incident: Incident,
    *,
    hypothesis_id: str,
    text: str,
    confidence: ConfidenceClass,
    evidence_for: list[str],
    evidence_against: list[str],
    unknowns: list[str],
    at: datetime,
    actor: str,
    note: str | None = None,
) -> tuple[Incident, IncidentEvent]:
    """Attach a candidate cause — `CORRELATED` or `HYPOTHESIZED` only.

    Confidence is explainable and evidence-bearing: `evidence_for` and
    `evidence_against` are explicit directional lists, `unknowns` records
    what the evidence cannot say. `evidence_refs` stays the flat union
    older readers consume.
    """
    if confidence not in CREATABLE_CONFIDENCE:
        raise ValidationError(
            "a hypothesis is created CORRELATED or HYPOTHESIZED — VERIFIED "
            "requires an independent verification record and REFUTED is a "
            "demotion outcome, never a starting point",
            details={
                "incident_id": incident.incident_id,
                "confidence": confidence.value,
            },
        )
    if any(h.hypothesis_id == hypothesis_id for h in incident.hypotheses):
        raise ValidationError(
            f"hypothesis already exists: {hypothesis_id}",
            details={"incident_id": incident.incident_id},
        )
    hypothesis = Hypothesis(
        hypothesis_id=hypothesis_id,
        text=text,
        confidence=confidence,
        evidence_refs=sorted(set(evidence_for) | set(evidence_against)),
        evidence_for=list(evidence_for),
        evidence_against=list(evidence_against),
        unknowns=list(unknowns),
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
            "evidence_for": ",".join(evidence_for),
            "evidence_against": ",".join(evidence_against),
        },
        discriminator=hypothesis_id,
    )
    return updated, event


def promote_hypothesis(
    incident: Incident,
    hypothesis_id: str,
    *,
    verification_id: str,
    at: datetime,
    actor: str,
    note: str | None = None,
) -> tuple[Incident, IncidentEvent]:
    """Promote a hypothesis to VERIFIED — only on independent evidence.

    The `verification_id` must name a `VerificationRecord` persisted on
    this incident, bound to the same hypothesis, whose verdict is
    `VERIFIED`. Anything else is a named refusal — VERIFIED is never
    self-declared (docs/09).
    """
    found = next(
        (h for h in incident.hypotheses if h.hypothesis_id == hypothesis_id),
        None,
    )
    if found is None:
        raise NotFoundError(
            f"hypothesis not found: {hypothesis_id}",
            details={"incident_id": incident.incident_id},
        )
    if found.confidence is ConfidenceClass.VERIFIED:
        raise ValidationError(
            f"hypothesis already VERIFIED: {hypothesis_id}",
            details={"incident_id": incident.incident_id},
        )
    if found.confidence is ConfidenceClass.REFUTED:
        raise ValidationError(
            "a REFUTED hypothesis cannot be promoted — demotion is "
            "terminal; open a new hypothesis with its own evidence",
            details={
                "incident_id": incident.incident_id,
                "hypothesis_id": hypothesis_id,
            },
        )
    record = next(
        (
            v
            for v in incident.verifications
            if v.verification_id == verification_id and v.hypothesis_id == hypothesis_id
        ),
        None,
    )
    if record is None:
        raise ValidationError(
            "VERIFIED requires a persisted verification for this "
            "hypothesis — run a verification plan first",
            details={
                "incident_id": incident.incident_id,
                "hypothesis_id": hypothesis_id,
                "verification_id": verification_id,
            },
        )
    if record.verdict != "VERIFIED":
        raise ValidationError(
            f"verification {verification_id} verdict is {record.verdict}, "
            "not VERIFIED — promotion requires independent evidence",
            details={
                "incident_id": incident.incident_id,
                "verification_id": verification_id,
                "verdict": record.verdict,
            },
        )
    evidence = sorted(
        {
            ref
            for receipt in record.receipts
            for ref in receipt.get("evidence_refs", "").split(",")
            if ref
        }
        | set(found.evidence_for)
    )
    updated_hyp = found.model_copy(
        update={
            "confidence": ConfidenceClass.VERIFIED,
            "evidence_refs": evidence,
            "evidence_for": sorted(set(found.evidence_for) | {f"verification:{verification_id}"}),
            "verification_id": verification_id,
            "note": note or found.note,
            "updated_at": at,
        }
    )
    hypotheses = [
        updated_hyp if h.hypothesis_id == hypothesis_id else h for h in incident.hypotheses
    ]
    updated = _bump(incident, hypotheses=hypotheses)
    event = make_event(
        incident_id=incident.incident_id,
        kind=EventKind.HYPOTHESIS_UPDATED,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=note or f"hypothesis VERIFIED via {verification_id}",
        details={
            "hypothesis_id": hypothesis_id,
            "from_confidence": found.confidence.value,
            "to_confidence": ConfidenceClass.VERIFIED.value,
            "verification_id": verification_id,
        },
        discriminator=f"{hypothesis_id}|{found.confidence.value}>VERIFIED",
    )
    return updated, event


def demote_hypothesis(
    incident: Incident,
    hypothesis_id: str,
    *,
    reason: str,
    evidence_against: list[str] | None = None,
    at: datetime,
    actor: str,
    note: str | None = None,
) -> tuple[Incident, IncidentEvent]:
    """Demote a hypothesis to REFUTED — the terminal contradiction state."""
    if not reason.strip():
        raise ValidationError("demote requires a recorded reason")
    found = next(
        (h for h in incident.hypotheses if h.hypothesis_id == hypothesis_id),
        None,
    )
    if found is None:
        raise NotFoundError(
            f"hypothesis not found: {hypothesis_id}",
            details={"incident_id": incident.incident_id},
        )
    if found.confidence is ConfidenceClass.REFUTED:
        raise ValidationError(
            f"hypothesis already REFUTED: {hypothesis_id}",
            details={"incident_id": incident.incident_id},
        )
    against = sorted(set(found.evidence_against) | set(evidence_against or []))
    updated_hyp = found.model_copy(
        update={
            "confidence": ConfidenceClass.REFUTED,
            "evidence_against": against,
            "evidence_refs": sorted(set(found.evidence_for) | set(against)),
            "note": note or reason,
            "updated_at": at,
        }
    )
    hypotheses = [
        updated_hyp if h.hypothesis_id == hypothesis_id else h for h in incident.hypotheses
    ]
    updated = _bump(incident, hypotheses=hypotheses)
    event = make_event(
        incident_id=incident.incident_id,
        kind=EventKind.HYPOTHESIS_UPDATED,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=note or f"hypothesis REFUTED: {reason}",
        details={
            "hypothesis_id": hypothesis_id,
            "from_confidence": found.confidence.value,
            "to_confidence": ConfidenceClass.REFUTED.value,
            "reason": reason,
        },
        discriminator=f"{hypothesis_id}|{found.confidence.value}>REFUTED",
    )
    return updated, event


def record_verification(
    incident: Incident,
    *,
    record: VerificationRecord,
    at: datetime,
    actor: str,
) -> tuple[Incident, IncidentEvent]:
    """Persist a verification outcome — journaled, deduped by id."""
    if any(v.verification_id == record.verification_id for v in incident.verifications):
        return incident, make_event(  # replay is a no-op on the row
            incident_id=incident.incident_id,
            kind=EventKind.NOTE,
            at=at,
            actor=actor,
            from_status=incident.status,
            to_status=incident.status,
            note=f"verification {record.verification_id} already recorded",
            discriminator=f"verification_dup|{record.verification_id}",
        )
    updated = _bump(incident, verifications=[*incident.verifications, record])
    event = make_event(
        incident_id=incident.incident_id,
        kind=EventKind.VERIFICATION_RECORDED,
        at=at,
        actor=actor,
        from_status=incident.status,
        to_status=incident.status,
        note=(
            f"verification {record.verification_id} → {record.verdict} for {record.hypothesis_id}"
        ),
        details={
            "verification_id": record.verification_id,
            "hypothesis_id": record.hypothesis_id,
            "verdict": record.verdict,
            "receipts": str(len(record.receipts)),
        },
        discriminator=f"verification|{record.verification_id}",
    )
    return updated, event


# ---------------------------------------------------------------------------
# Use cases — UnitOfWork orchestration for the governed mutation paths.


class AddHypothesis:
    def __init__(self, uow: UnitOfWork, clock_now: datetime, ids: IdFactory) -> None:
        self._uow, self._now, self._ids = uow, clock_now, ids

    def execute(
        self,
        *,
        incident_id: str,
        text: str,
        confidence: str | ConfidenceClass,
        evidence_for: list[str] | None = None,
        evidence_against: list[str] | None = None,
        unknowns: list[str] | None = None,
        actor: str = "cli",
        note: str | None = None,
    ) -> tuple[Hypothesis, Incident]:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            hypothesis_id = hypothesis_id_for(incident_id=incident_id, text=text, at=self._now)
            updated, event = create_hypothesis(
                incident,
                hypothesis_id=hypothesis_id,
                text=text,
                confidence=ConfidenceClass(confidence),
                evidence_for=evidence_for or [],
                evidence_against=evidence_against or [],
                unknowns=unknowns or [],
                at=self._now,
                actor=actor,
                note=note,
            )
            uow.incidents.put(updated, expected_revision=incident.revision)
            uow.incident_events.append(event)
            uow.commit()
            return updated.hypotheses[-1], updated


class PromoteHypothesis:
    def __init__(self, uow: UnitOfWork, clock_now: datetime) -> None:
        self._uow, self._now = uow, clock_now

    def execute(
        self,
        *,
        incident_id: str,
        hypothesis_id: str,
        verification_id: str,
        actor: str = "cli",
        note: str | None = None,
    ) -> tuple[Hypothesis, Incident]:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            updated, event = promote_hypothesis(
                incident,
                hypothesis_id,
                verification_id=verification_id,
                at=self._now,
                actor=actor,
                note=note,
            )
            uow.incidents.put(updated, expected_revision=incident.revision)
            uow.incident_events.append(event)
            uow.commit()
            hypothesis = next(h for h in updated.hypotheses if h.hypothesis_id == hypothesis_id)
            return hypothesis, updated


class DemoteHypothesis:
    def __init__(self, uow: UnitOfWork, clock_now: datetime) -> None:
        self._uow, self._now = uow, clock_now

    def execute(
        self,
        *,
        incident_id: str,
        hypothesis_id: str,
        reason: str,
        evidence_against: list[str] | None = None,
        actor: str = "cli",
        note: str | None = None,
    ) -> tuple[Hypothesis, Incident]:
        with self._uow as uow:
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            updated, event = demote_hypothesis(
                incident,
                hypothesis_id,
                reason=reason,
                evidence_against=evidence_against,
                at=self._now,
                actor=actor,
                note=note,
            )
            uow.incidents.put(updated, expected_revision=incident.revision)
            uow.incident_events.append(event)
            uow.commit()
            hypothesis = next(h for h in updated.hypotheses if h.hypothesis_id == hypothesis_id)
            return hypothesis, updated
