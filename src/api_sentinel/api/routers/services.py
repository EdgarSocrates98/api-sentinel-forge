"""`/v1/services*` — real inventory, honest health, explicit metric units."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from api_sentinel.api.deps import (
    ApiContext,
    get_ctx,
    get_uow,
    require_scope,
)
from api_sentinel.api.schemas import (
    MetricsResponse,
    OperationListResponse,
    ServiceDetailResponse,
    ServiceListResponse,
    SloPanelResponse,
)
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.application.use_cases import (
    GetServiceDetail,
    GetServiceLinks,
    GetServiceMetrics,
    GetServiceSlos,
    ListServiceOperations,
    ListServiceSummaries,
)

router = APIRouter(tags=["services"])


@router.get("/v1/services")
def list_services(
    ctx: Annotated[ApiContext, Depends(get_ctx)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("query"))],
) -> ServiceListResponse:
    """Every registered service with coverage + honest health — real
    inventory only, never demo data."""
    return ServiceListResponse(
        services=ListServiceSummaries(uow, ctx.clock).execute()
    )


@router.get("/v1/services/{service_id}")
def service_detail(
    service_id: str,
    ctx: Annotated[ApiContext, Depends(get_ctx)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("query"))],
) -> ServiceDetailResponse:
    """Full health + coverage + the external-links verdict.

    `links.correspondence` activates the Product Forge experience only on a
    *proven* (verified-provenance) identity — name matching never does.
    """
    detail = GetServiceDetail(uow, ctx.clock).execute(service_id=service_id)
    links = GetServiceLinks(uow).execute(service_id=service_id)
    unknowns = sorted(set(detail.health.unknowns) | set(links.unknowns))
    return ServiceDetailResponse(
        detail=detail, links=links, unknowns=unknowns
    )


@router.get("/v1/services/{service_id}/operations")
def service_operations(
    service_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("query"))],
) -> OperationListResponse:
    """Identified operations — declared inventory and observed series kept
    distinct via `provenance`."""
    return OperationListResponse(
        operations=ListServiceOperations(uow).execute(service_id=service_id)
    )


@router.get("/v1/services/{service_id}/metrics")
def service_metrics(
    service_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("query"))],
    window_start: Annotated[datetime | None, Query()] = None,
    window_end: Annotated[datetime | None, Query()] = None,
    operation_id: Annotated[str | None, Query()] = None,
    max_windows: Annotated[int, Query(ge=1, le=240)] = 60,
) -> MetricsResponse:
    """Aggregated series from real ingested observations: explicit units,
    per-window quality, visible gaps, excluded-by-reason sampling flags."""
    metrics = GetServiceMetrics(uow).execute(
        service_id=service_id,
        window_start=window_start,
        window_end=window_end,
        operation_id=operation_id,
        max_windows=max_windows,
    )
    return MetricsResponse(metrics=metrics, unknowns=list(metrics.unknowns))


@router.get("/v1/services/{service_id}/slos")
def service_slos(
    service_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("query"))],
) -> SloPanelResponse:
    """Objectives, budgets and windowed history — each assessment pinned to
    the definition revision that produced it."""
    panel = GetServiceSlos(uow).execute(service_id=service_id)
    return SloPanelResponse(panel=panel, unknowns=list(panel.unknowns))
