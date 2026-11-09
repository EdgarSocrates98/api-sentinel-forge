"""Integration use cases — orchestrate ports + adapters; injected clock/ids.

`RecordChange`/`ListChanges`/`RecordDependency` own the change and
operational-graph persistence; `DelegateToDoctor` builds the versioned
handoff, dispatches through `DoctorACL` and records the audit row +
receipt; `Correlate` assembles the LAB-003 report — observed edges, the
structural context (when Atlas is wired) and honest unknowns, never causal
certainty.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from pydantic import Field

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.domain import (
    ChangeEvent,
    ChangeKind,
    Clock,
    DependencyObservation,
    ExternalHandoff,
    HandoffStatus,
    IdFactory,
    NotFoundError,
)
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.integrations.atlas.file_adapter import FileStructuralContext
from api_sentinel.integrations.atlas.models import StructuralRef
from api_sentinel.integrations.atlas.port import StructuralContextPort
from api_sentinel.integrations.correlate import CorrelationReport, correlate_edges
from api_sentinel.integrations.doctor.adapter import DoctorACL, DoctorMode
from api_sentinel.integrations.doctor.translate import (
    DOCTOR_FORGE,
    DOCTOR_INVESTIGATION_KIND,
    DoctorIngest,
)
from api_sentinel.integrations.errors import IntegrationUnavailable


def _dep_edge_id(
    service_id: str,
    target: str,
    operation_id: str | None,
    window_start: datetime,
    window_end: datetime,
) -> str:
    """Deterministic edge identity — re-ingesting a window is idempotent."""
    canonical = "|".join(
        (service_id, target, operation_id or "",
         window_start.isoformat(), window_end.isoformat())
    )
    return "dep_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _change_id(
    service_id: str, kind: ChangeKind, at: datetime, ref: str | None,
) -> str:
    """Deterministic change identity — same change re-imported is a no-op."""
    canonical = "|".join(
        (service_id, kind.value, at.isoformat(), ref or "")
    )
    return "chg_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class RecordChange:
    """Idempotent: same (service, kind, at, ref) returns the same event."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(
        self,
        *,
        service_id: str,
        kind: ChangeKind | str,
        at: datetime,
        source: str = "cli",
        ref: str | None = None,
        details: dict[str, str] | None = None,
    ) -> ChangeEvent:
        kind = ChangeKind(kind)
        with self._uow:
            if self._uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            cid = _change_id(service_id, kind, at, ref)
            existing = self._uow.change_events.get(cid)
            if existing is not None:
                return existing
            event = ChangeEvent(
                change_id=cid,
                service_id=service_id,
                kind=kind,
                at=at,
                source=source,
                ref=ref,
                details=details or {},
                created_at=self._clock.now(),
            )
            self._uow.change_events.put(event)
            self._uow.commit()
            return event


class ListChanges:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        *,
        service_id: str,
        at_start: datetime | None = None,
        at_end: datetime | None = None,
    ) -> list[ChangeEvent]:
        with self._uow:
            if self._uow.services.get(service_id) is None:
                raise NotFoundError(f"service not found: {service_id}")
            return self._uow.change_events.list_for_service(
                service_id, at_start, at_end
            )


class RecordDependency:
    """Idempotent: same (service, target, op, window) accumulates counters."""

    def __init__(self, uow: UnitOfWork, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(
        self,
        *,
        service_id: str,
        target: str,
        window_start: datetime,
        window_end: datetime,
        target_kind: str = "service",
        operation_id: str | None = None,
        calls: int = 0,
        errors: int = 0,
        timeouts: int = 0,
        evidence_refs: list[str] | None = None,
    ) -> DependencyObservation:
        edge = DependencyObservation(
            edge_id=_dep_edge_id(
                service_id, target, operation_id, window_start, window_end
            ),
            service_id=service_id,
            target=target,
            target_kind=target_kind,
            operation_id=operation_id,
            window_start=window_start,
            window_end=window_end,
            calls=calls,
            errors=errors,
            timeouts=timeouts,
            evidence_refs=evidence_refs or [],
            provenance="observed",
            created_at=self._clock.now(),
        )
        with self._uow:
            self._uow.dependency_observations.put(edge)
            self._uow.commit()
            return edge


class ListDependencies:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def execute(
        self,
        *,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> list[DependencyObservation]:
        with self._uow:
            return self._uow.dependency_observations.list_for_service(
                service_id, window_start, window_end
            )


class DelegationOutcome(FrozenModel):
    """What `DelegateToDoctor` produced — the auditable record + result."""

    handoff: ExternalHandoff
    status: str
    mode: str
    request_ref: str | None = None
    response_ref: str | None = None
    ingest: DoctorIngest | None = None
    detail: str = ""
    unknowns: list[str] = Field(default_factory=list)


class DelegateToDoctor:
    """Build the versioned handoff, dispatch through `DoctorACL`, audit it.

    Every attempt — success, refusal, timeout, unavailability — writes an
    `ExternalHandoff` row and a redacted receipt; the returned record's
    `status` is the named terminal state (never a simulated success).
    """

    def __init__(
        self,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdFactory,
        acl: DoctorACL | None = None,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        self._uow, self._clock, self._ids = uow, clock, ids
        self._acl = acl or DoctorACL()
        self._ws_root = workspace_root

    def execute(
        self,
        *,
        service_id: str,
        target: str,
        capabilities: list[str] | None = None,
        context_root: Path | None = None,
        clock_iso: str | None = None,
        kind: str = DOCTOR_INVESTIGATION_KIND,
    ) -> DelegationOutcome:
        now = self._clock.now()
        with self._uow:
            service = self._uow.services.get(service_id)
            if service is None:
                raise NotFoundError(f"service not found: {service_id}")
            anomalies = self._uow.anomalies.list_for_service(service_id)
            evidence_refs = sorted(
                {r for a in anomalies for r in a.evidence_refs}
            )
            handoff_id = f"hnd_{self._ids.new('h')}"
            handoff = InvestigationHandoffV1(
                schema_=InvestigationHandoffV1.expected_schema,
                handoff_id=handoff_id,
                from_forge="api-sentinel-forge",
                to_forge=DOCTOR_FORGE,
                kind=kind,
                correlation_id=handoff_id,
                payload={
                    "service_id": service_id,
                    "service_name": service.service_name,
                    "capabilities": capabilities or ["api.diagnose"],
                    "anomaly_ids": [a.anomaly_id for a in anomalies],
                },
                constraints={
                    "evidence_refs": evidence_refs,
                    "budget_seconds": self._acl._timeout,
                    "scopes": ["read:api-evidence"],
                },
            )

        receipts_dir = self._receipts_dir()
        handoffs_dir = self._handoffs_dir()
        try:
            result = self._acl.delegate(
                handoff,
                target=target,
                capabilities=capabilities,
                context_root=context_root,
                clock=clock_iso,
                receipts_dir=receipts_dir,
                handoffs_dir=handoffs_dir,
                now_iso=now.isoformat(),
            )
        except IntegrationUnavailable as exc:
            record = ExternalHandoff(
                handoff_id=handoff_id,
                to_forge=DOCTOR_FORGE,
                kind=kind,
                status=HandoffStatus.UNAVAILABLE,
                detail=exc.reason,
                created_at=now,
            )
            self._persist(record)
            return DelegationOutcome(
                handoff=record,
                status=record.status.value,
                mode=self._acl.mode.value,
                detail=exc.reason,
            )

        status_map = {
            "delegated": HandoffStatus.DELEGATED,
            "completed": HandoffStatus.COMPLETED,
            "refused": HandoffStatus.REFUSED,
            "timed_out": HandoffStatus.TIMED_OUT,
            "unavailable": HandoffStatus.UNAVAILABLE,
            "failed": HandoffStatus.FAILED,
        }
        record = ExternalHandoff(
            handoff_id=handoff_id,
            to_forge=DOCTOR_FORGE,
            kind=kind,
            status=status_map.get(result.status, HandoffStatus.FAILED),
            request_ref=result.request_ref,
            response_ref=result.response_ref,
            detail=result.detail,
            created_at=now,
        )
        self._persist(record)
        return DelegationOutcome(
            handoff=record,
            status=record.status.value,
            mode=result.mode.value,
            request_ref=result.request_ref,
            response_ref=result.response_ref,
            ingest=result.ingest,
            detail=result.detail,
        )

    def _persist(self, record: ExternalHandoff) -> None:
        with self._uow:
            self._uow.handoff_records.put(record)
            self._uow.commit()

    def _receipts_dir(self) -> Path | None:
        return (self._ws_root / "receipts") if self._ws_root else None

    def _handoffs_dir(self) -> Path | None:
        return (self._ws_root / "handoffs") if self._ws_root else None


class Correlate:
    """The LAB-003 correlation report — two graphs kept distinct.

    `structural` is an optional `StructuralContextPort`: when provided the
    service's declared `code_atlas_identity` resolves to an Atlas entity and
    its outgoing structural edges land in `structural_edges` — always
    labeled `structural`, never merged with the observed operational edges.
    An absent/unresolved Atlas produces an honest unknown, not a crash.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        structural: StructuralContextPort | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._uow = uow
        self._structural = structural
        self._clock = clock

    def execute(
        self,
        *,
        service_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> CorrelationReport:
        unknowns: list[str] = []
        with self._uow:
            service = self._uow.services.get(service_id)
            if service is None:
                raise NotFoundError(f"service not found: {service_id}")
            anomalies = self._uow.anomalies.list_for_service(service_id)
            observed = self._uow.dependency_observations.list_for_service(
                service_id, window_start, window_end
            )
            incidents = self._uow.incidents.list(service_id=service_id)
            incident = next(
                (i for i in incidents if i.status.value in {
                    "DETECTED", "TRIAGED", "INVESTIGATING",
                    "MITIGATING", "MONITORING", "REOPENED",
                }),
                incidents[0] if incidents else None,
            )

        anomaly_ids = [a.anomaly_id for a in anomalies]
        evidence_refs = sorted(
            {r for a in anomalies for r in a.evidence_refs}
            | {r for e in observed for r in e.evidence_refs}
        )
        observed_tuples = [
            (e.service_id, e.target,
             f"{e.calls} calls/{e.errors} errors/{e.timeouts} timeouts")
            for e in observed
        ]

        structural_tuples: list[tuple[str, str, str]] = []
        atlas_ref: StructuralRef | None = None
        if self._structural is not None:
            atlas_ref = self._structural.resolve_service(service)
            if atlas_ref is None:
                unknowns.append(
                    f"service {service_id}: no code_atlas_identity ref — "
                    "structural identity unresolved (ADR-012), no "
                    "name-matched guess made"
                )
            else:
                for edge in self._structural.dependencies(atlas_ref.atlas_id):
                    structural_tuples.append(
                        (edge.from_ref, edge.to_ref,
                         f"{edge.kind} ({edge.resolver or edge.confidence})")
                    )
        else:
            unknowns.append(
                "no structural context configured — structural graph "
                "unavailable; correlation runs on observed edges only"
            )

        suspected = self._suspected_origin(observed)
        return correlate_edges(
            service_id=service_id,
            anomaly_ids=anomaly_ids,
            observed=observed_tuples,
            structural=structural_tuples,
            suspected=suspected,
            unknowns=unknowns,
            evidence_refs=evidence_refs,
            incident_id=incident.incident_id if incident else None,
        )

    @staticmethod
    def _suspected_origin(
        observed: list[DependencyObservation],
    ) -> str | None:
        """The observed edge with the most errors/timeouts — the LAB-003
        dependency. Returns `target` only; never asserts causality."""
        if not observed:
            return None
        worst = max(
            observed,
            key=lambda e: (e.errors + e.timeouts, e.calls, e.target),
        )
        if worst.errors + worst.timeouts == 0:
            return None
        return worst.target


def load_structural(
    config: dict[str, object],
    workspace_root: Path,
) -> StructuralContextPort | None:
    """Resolve the Atlas adapter from workspace config — absent → `None`.

    `integrations.atlas.export` names the exported manifest (workspace-
    relative or absolute); a missing/invalid file raises
    `IntegrationUnavailable` — never a fabricated empty graph.
    """
    integrations = config.get("integrations") if isinstance(config, dict) else None
    if not isinstance(integrations, dict):
        return None
    atlas = integrations.get("atlas")
    if not isinstance(atlas, dict):
        return None
    export = atlas.get("export")
    if not export:
        return None
    path = Path(str(export))
    if not path.is_absolute():
        path = workspace_root / path
    return FileStructuralContext.load(path)


def doctor_acl_from_config(
    config: dict[str, object],
) -> DoctorACL:
    """Resolve the Doctor adapter: explicit `mode`/`timeout_seconds` from
    `integrations.doctor`, auto-selecting in-process when importable."""
    integrations = config.get("integrations") if isinstance(config, dict) else None
    doctor = (
        integrations.get("doctor")
        if isinstance(integrations, dict) else None
    )
    if not isinstance(doctor, dict):
        return DoctorACL()
    mode = doctor.get("mode")
    timeout = float(doctor.get("timeout_seconds", 30.0))
    return DoctorACL(
        mode=DoctorMode(str(mode)) if mode else None,
        timeout_seconds=timeout,
    )
