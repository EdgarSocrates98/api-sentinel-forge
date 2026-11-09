"""`/v1/incidents*` — lifecycle detail, timeline, controlled investigations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from api_sentinel.api.deps import (
    ApiContext,
    acquire_job_slot,
    get_ctx,
    get_uow,
    require_scope,
)
from api_sentinel.api.schemas import (
    IncidentDetailResponse,
    IncidentListResponse,
    IncidentTimelineResponse,
    InvestigationRequest,
    InvestigationResponse,
)
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.application.use_cases import (
    GetIncidentDetail,
    GetIncidentTimeline,
    ListIncidents,
    StartInvestigation,
)
from api_sentinel.domain import ValidationError
from api_sentinel.incidents import IncidentStatus

router = APIRouter(tags=["incidents"])


@router.get("/v1/incidents")
def list_incidents(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str | None, Query()] = None,
    service_id: Annotated[str | None, Query()] = None,
) -> IncidentListResponse:
    """Active + historical incidents; `status=open` filters to unresolved."""
    open_only = False
    parsed: IncidentStatus | None = None
    if status is not None:
        if status.strip().lower() == "open":
            open_only = True
        else:
            try:
                parsed = IncidentStatus(status.strip().upper())
            except ValueError as exc:
                raise ValidationError(
                    f"invalid status: {status!r}",
                    details={
                        "expected": [
                            "open",
                            *sorted(s.value for s in IncidentStatus),
                        ]
                    },
                ) from exc
    incidents = ListIncidents(uow).execute(
        status=parsed, service_id=service_id, open_only=open_only
    )
    evidence_refs = sorted({r for i in incidents for r in i.evidence_refs})
    return IncidentListResponse(incidents=incidents, evidence_refs=evidence_refs)


@router.get("/v1/incidents/{incident_id}")
def incident_detail(
    incident_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> IncidentDetailResponse:
    """Investigation view: row + journal + anomalies + resolvable evidence
    metadata — unresolved refs named under `evidence_unresolved`."""
    detail = GetIncidentDetail(uow).execute(incident_id=incident_id)
    unknowns = [
        f"evidence_unresolved:{r}" for r in detail.evidence_unresolved
    ]
    evidence_refs = sorted(
        {a.evidence_id for a in detail.evidence}
        | set(detail.evidence_unresolved)
    )
    return IncidentDetailResponse(
        detail=detail, unknowns=unknowns, evidence_refs=evidence_refs
    )


@router.get("/v1/incidents/{incident_id}/timeline")
def incident_timeline(
    incident_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> IncidentTimelineResponse:
    """Journal events + related changes/observed edges since first
    observation — a clipped relation set is marked `truncated`."""
    timeline = GetIncidentTimeline(uow).execute(incident_id=incident_id)
    evidence_refs = sorted(
        set(timeline.incident.evidence_refs)
        | {r for e in timeline.dependency_observations for r in e.evidence_refs}
    )
    return IncidentTimelineResponse(
        timeline=timeline,
        unknowns=list(timeline.unknowns),
        evidence_refs=evidence_refs,
    )


@router.post("/v1/incidents/{incident_id}/investigations", status_code=201)
def start_investigation(
    incident_id: str,
    body: InvestigationRequest,
    response: Response,
    ctx: Annotated[ApiContext, Depends(get_ctx)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("investigate"))],
) -> InvestigationResponse:
    """Assemble the bounded context pack synchronously: incident + journal +
    anomalies + evidence refs + latest indicators + structural context when
    Atlas has usable data — `UNAVAILABLE`/`UNRESOLVED`, never fabricated.
    A replayed key returns the persisted investigation with 200."""
    release = acquire_job_slot(ctx)
    try:
        investigation, replayed = StartInvestigation(
            uow, ctx.clock, ctx.ids,
            structural=ctx.structural,
            structural_reason=ctx.structural_reason,
        ).execute(
            incident_id=incident_id,
            idempotency_key=body.idempotency_key,
            actor=actor,
            note=body.note,
        )
    finally:
        release()
    if replayed:
        response.status_code = 200
    evidence_refs = sorted(
        set(investigation.result.get("evidence_refs", []))
    )
    return InvestigationResponse(
        investigation=investigation,
        replayed=replayed,
        evidence_refs=evidence_refs,
    )
