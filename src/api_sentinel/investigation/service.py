"""Agentic investigation orchestration (Cycle 8, ADR-010).

`RunAgenticInvestigation` is the governed path: assemble a bounded
`ContextPack`, persist it on an `Investigation` audit row, then attempt
a session through the injected `AgentSessionPort`. The session is a
governed seam — Bridge owns the runtime; an absent Bridge yields a named
`unavailable` result and the deterministic investigation still stands
(degradation contract, docs/06).

Every attempt is audited three ways: the investigation row, a
`session_requested` journal entry on the incident, and an
`ExternalHandoff` record (to_forge=forge-agent-bridge) — never silent.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from api_sentinel.application.jobs import (
    Investigation,
    InvestigationStatus,
    StructuralContextView,
)
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.domain.errors import NotFoundError
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.domain.ids import IdFactory
from api_sentinel.domain.models import AuditEvent, ExternalHandoff, HandoffStatus
from api_sentinel.incidents.journal import EventKind, make_event
from api_sentinel.incidents.models import Incident
from api_sentinel.integrations.atlas.port import StructuralContextPort
from api_sentinel.investigation.agent_session import (
    AgentSessionPort,
    AgentSessionResult,
    build_session_request,
    session_result_summary,
)
from api_sentinel.investigation.changes import correlate_changes
from api_sentinel.investigation.context_pack import (
    DEFAULT_BUDGET_BYTES,
    ContextPack,
    build_context_pack,
)
from api_sentinel.reliability import HealthPolicy, assess_service_health

BRIDGE_FORGE = "forge-agent-bridge"

_HANDOFF_STATUS = {
    "delegated": HandoffStatus.DELEGATED,
    "unavailable": HandoffStatus.UNAVAILABLE,
    "refused": HandoffStatus.REFUSED,
    "failed": HandoffStatus.FAILED,
}

_EVENT_CAP = 50
_ANOMALY_CAP = 50
_EVIDENCE_CAP = 100
_CHANGE_CAP = 100


class AgenticInvestigationOutcome(FrozenModel):
    """What one governed investigation produced — pack + session attempt."""

    investigation: Investigation
    pack: ContextPack
    session: AgentSessionResult
    replayed: bool = False


class RunAgenticInvestigation:
    """Bounded pack → governed session attempt → audited, journaled record.

    `session_port` is injected (file seam or named-unavailable adapter);
    `structural` is the optional Atlas port, resolved by the caller.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock_now: datetime,
        ids: IdFactory,
        *,
        session_port: AgentSessionPort,
        structural: StructuralContextPort | None = None,
        structural_reason: str = "",
        workspace_root: Path | None = None,
        budget_bytes: int = DEFAULT_BUDGET_BYTES,
    ) -> None:
        self._uow, self._now, self._ids = uow, clock_now, ids
        self._session_port = session_port
        self._structural = structural
        self._structural_reason = structural_reason
        self._ws_root = workspace_root
        self._budget = budget_bytes

    def execute(
        self,
        *,
        incident_id: str,
        principal: str,
        workspace: str,
        provider_choice: str | None = None,
        model: str | None = None,
        cost_limit: float | None = None,
        approvals: list[str] | tuple[str, ...] = (),
        idempotency_key: str | None = None,
        actor: str = "cli",
    ) -> AgenticInvestigationOutcome:
        with self._uow as uow:
            if idempotency_key:
                existing = uow.investigations.get_by_idempotency_key(
                    idempotency_key
                )
                if existing is not None:
                    pack = ContextPack.model_validate(existing.context_pack)
                    session = AgentSessionResult(
                        status="delegated",
                        session_ref=None,
                        detail="replayed idempotency key — prior attempt "
                               "returned verbatim",
                        session_request_id="",
                        governance=_replay_governance(
                            existing, principal, workspace
                        ),
                    )
                    return AgenticInvestigationOutcome(
                        investigation=existing,
                        pack=pack,
                        session=session,
                        replayed=True,
                    )
            incident = uow.incidents.get(incident_id)
            if incident is None:
                raise NotFoundError(f"incident not found: {incident_id}")
            pack = self._build_pack(uow, incident)

        session = self._open_session(
            pack,
            principal=principal,
            workspace=workspace,
            provider_choice=provider_choice,
            model=model,
            cost_limit=cost_limit,
            approvals=approvals,
        )
        return self._persist(
            incident_id=incident_id,
            pack=pack,
            session=session,
            idempotency_key=idempotency_key,
            actor=actor,
        )

    # -- pack assembly -------------------------------------------------

    def _build_pack(self, uow: UnitOfWork, incident: Incident) -> ContextPack:
        anomalies = uow.anomalies.list_for_incident(incident.incident_id)
        anomalies = anomalies[-_ANOMALY_CAP:]
        events = uow.incident_events.list(incident.incident_id)
        events = events[-_EVENT_CAP:]
        refs = sorted(
            set(incident.evidence_refs)
            | {r for a in anomalies for r in a.evidence_refs}
        )[:_EVIDENCE_CAP]
        evidence = [
            a for a in (uow.evidence.get(r) for r in refs) if a is not None
        ]
        unresolved = [r for r in refs if uow.evidence.get(r) is None]
        source_ids = [
            s.source_id for s in uow.sources.list(incident.service_id)
        ]
        observations = uow.observations.list_for_service(incident.service_id)
        health = assess_service_health(
            service_id=incident.service_id,
            observations=observations,
            source_ids=source_ids,
            now=self._now,
            policy=HealthPolicy(),
        )
        structural = self._structural_view(uow, incident)
        changes = uow.change_events.list_for_service(
            incident.service_id
        )[:_CHANGE_CAP]
        unknowns: list[str] = []
        unknowns.extend(f"evidence_unresolved:{r}" for r in unresolved)
        if structural.state != "AVAILABLE":
            unknowns.append(f"structural_context:{structural.reason}")
        # correlated changes join the pack *with* the honest disclaimer —
        # the agent sees ranked candidates, never a causal claim; changes
        # after the anchor are excluded and named in unknowns
        correlation = correlate_changes(incident=incident, changes=changes)
        unknowns.extend(correlation.unknowns)
        pack = build_context_pack(
            incident=incident,
            anomalies=anomalies,
            events=events,
            evidence=evidence,
            indicators=health,
            structural=structural,
            change_events=[
                c.change for c in correlation.candidates
            ],
            log_excerpts=self._log_excerpts(uow, anomalies),
            built_at=self._now,
            budget_bytes=self._budget,
            unknowns=unknowns,
        )
        if correlation.candidates:
            pack = pack.model_copy(
                update={
                    "unknowns": sorted(
                        {
                            *pack.unknowns,
                            f"change_correlation:{correlation.proximity_ne_cause}",
                        }
                    )
                }
            )
        return pack

    def _log_excerpts(
        self, uow: UnitOfWork, anomalies: list[Any]
    ) -> list[tuple[str, str]]:
        """Redacted log excerpts — only `message`/`body`-style attributes.

        Payload capture is off by default (docs/07): an observation
        contributes an excerpt only when it carries a `log.message`
        attribute, and the excerpt is the redacted attribute text —
        bounded per item inside the pack builder.
        """
        excerpts: list[tuple[str, str]] = []
        for anomaly in anomalies:
            for ref in anomaly.evidence_refs:
                if not ref.startswith("obs:"):
                    continue
                obs = uow.observations.get(ref[4:])
                if obs is None:
                    continue
                message = obs.attributes.get("log.message")
                if isinstance(message, str) and message:
                    excerpts.append((ref, message))
        return excerpts[:20]

    def _structural_view(
        self, uow: UnitOfWork, incident: Incident
    ) -> StructuralContextView:
        if self._structural is None:
            return StructuralContextView(
                state="UNAVAILABLE",
                reason=self._structural_reason
                or "no structural context configured — structural graph "
                "unavailable",
            )
        service = uow.services.get(incident.service_id)
        if service is None:
            return StructuralContextView(
                state="UNAVAILABLE", reason="service row missing"
            )
        ref = self._structural.resolve_service(service)
        if ref is None:
            return StructuralContextView(
                state="UNRESOLVED",
                reason="no resolvable code_atlas_identity external_ref — "
                "structural identity unresolved (ADR-012); no name-matched "
                "guess made",
            )
        return StructuralContextView(
            state="AVAILABLE",
            reason=f"resolved via code_atlas_identity → {ref.atlas_id}",
            atlas_ref=ref,
            dependencies=self._structural.dependencies(ref.atlas_id),
            dependents=self._structural.dependents(ref.atlas_id),
        )

    # -- session attempt ------------------------------------------------

    def _open_session(
        self,
        pack: ContextPack,
        *,
        principal: str,
        workspace: str,
        provider_choice: str | None,
        model: str | None,
        cost_limit: float | None,
        approvals: list[str] | tuple[str, ...],
    ) -> AgentSessionResult:
        request = build_session_request(
            context_pack=pack,
            principal=principal,
            workspace=workspace,
            provider_choice=provider_choice,
            model=model,
            cost_limit=cost_limit,
            approvals=approvals,
        )
        return self._session_port.open_session(request)

    # -- persistence + audit --------------------------------------------

    def _persist(
        self,
        *,
        incident_id: str,
        pack: ContextPack,
        session: AgentSessionResult,
        idempotency_key: str | None,
        actor: str,
    ) -> AgenticInvestigationOutcome:
        summary = {
            "incident_id": incident_id,
            "pack_id": pack.pack_id,
            "pack_size_bytes": pack.size_bytes,
            "pack_truncated": pack.truncated,
            "session": session_result_summary(session),
            "unknowns": pack.unknowns,
        }
        investigation_id = (
            "inv_"
            + hashlib.sha256(
                f"{incident_id}|{idempotency_key}".encode()
            ).hexdigest()[:24]
            if idempotency_key
            else self._ids.new("inv")
        )
        with self._uow as uow:
            investigation = Investigation(
                investigation_id=investigation_id,
                incident_id=incident_id,
                status=InvestigationStatus.COMPLETED,
                result=summary,
                context_pack=pack.model_dump(mode="json"),
                idempotency_key=idempotency_key,
                created_at=self._now,
                finished_at=self._now,
            )
            uow.investigations.put(investigation)
            incident = uow.incidents.get(incident_id)
            if incident is not None:
                uow.incident_events.append(
                    make_event(
                        incident_id=incident_id,
                        kind=EventKind.SESSION_REQUESTED,
                        at=self._now,
                        actor=actor,
                        from_status=incident.status,
                        to_status=incident.status,
                        note=(
                            f"agent session {session.status}: "
                            f"{session.detail}"
                        ),
                        details={
                            "investigation_id": investigation_id,
                            "pack_id": pack.pack_id,
                            "session_status": session.status,
                            "session_ref": session.session_ref or "",
                            "principal": session.governance.principal,
                            "provider": session.governance.provider or "",
                            "cost_limit_usd": (
                                str(session.governance.cost_limit_usd)
                                if session.governance.cost_limit_usd
                                is not None
                                else ""
                            ),
                        },
                        discriminator=(
                            f"session|{investigation_id}|{session.status}"
                        ),
                    )
                )
            uow.handoff_records.put(
                ExternalHandoff(
                    handoff_id=f"hnd_{investigation_id}",
                    to_forge=BRIDGE_FORGE,
                    kind="agent-session",
                    status=_HANDOFF_STATUS.get(
                        session.status, HandoffStatus.FAILED
                    ),
                    request_ref=session.session_ref,
                    detail=session.detail,
                    created_at=self._now,
                )
            )
            uow.audit.append(
                AuditEvent(
                    audit_id=self._ids.new("aud"),
                    at=self._now,
                    actor=actor,
                    action="investigation.agentic",
                    target_type="incident",
                    target_id=incident_id,
                    details={
                        "investigation_id": investigation_id,
                        "session_status": session.status,
                    },
                )
            )
            uow.commit()
        return AgenticInvestigationOutcome(
            investigation=investigation,
            pack=pack,
            session=session,
        )


def _replay_governance(
    existing: Investigation, principal: str, workspace: str
) -> Any:
    """Governance echo for a replayed investigation — honest, not guessed."""
    from api_sentinel.investigation.agent_session import SessionGovernance

    session = (
        existing.result.get("session", {})
        if isinstance(existing.result, dict)
        else {}
    )
    return SessionGovernance(
        principal=str(session.get("principal") or principal),
        workspace=str(session.get("workspace") or workspace),
        provider=session.get("provider") or None,
        cost_limit_usd=(
            float(session["cost_limit_usd"])
            if session.get("cost_limit_usd") not in (None, "")
            else None
        ),
        approvals=[
            a for a in str(session.get("approvals", "")).split(",") if a
        ],
    )
