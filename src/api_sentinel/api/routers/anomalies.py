"""`/v1/anomalies` — the anomaly feed, filtered and bounded."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api_sentinel.api.deps import get_uow
from api_sentinel.api.schemas import AnomalyListResponse
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.application.use_cases import ListAnomalies

router = APIRouter(tags=["anomalies"])


@router.get("/v1/anomalies")
def list_anomalies(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    service_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> AnomalyListResponse:
    """Persisted anomalies — OBSERVED→VERIFIED states, bounded result set;
    `truncated` marks a clipped feed rather than hiding it."""
    rows, truncated = ListAnomalies(uow).execute(
        service_id=service_id, status=status, limit=limit
    )
    evidence_refs = sorted({r for a in rows for r in a.evidence_refs})
    return AnomalyListResponse(
        anomalies=rows, truncated=truncated, evidence_refs=evidence_refs
    )
