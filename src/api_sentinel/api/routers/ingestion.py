"""`/v1/ingestion/jobs*` — bounded synchronous imports, audited + idempotent."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from api_sentinel.api.deps import (
    ApiContext,
    acquire_job_slot,
    get_ctx,
    get_uow,
    require_scope,
)
from api_sentinel.api.schemas import (
    IngestionJobRequest,
    JobResponse,
)
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.application.use_cases import GetIngestionJob, StartIngestionJob

router = APIRouter(tags=["ingestion"])


@router.post("/v1/ingestion/jobs", status_code=201)
def start_ingestion_job(
    body: IngestionJobRequest,
    response: Response,
    ctx: Annotated[ApiContext, Depends(get_ctx)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("import"))],
) -> JobResponse:
    """Run the real pipeline inside the request (bounded): the persisted job
    carries honest counters; a replayed idempotency key returns the same job
    with 200 — never a duplicate import."""
    release = acquire_job_slot(ctx)
    try:
        job, replayed = StartIngestionJob(
            uow, ctx.clock, ctx.ids, ctx.redactor,
            workspace_root=ctx.workspace.root,
        ).execute(
            source_id=body.source_id,
            file_ref=body.file_ref,
            kind=body.kind,
            idempotency_key=body.idempotency_key,
            actor=actor,
        )
    finally:
        release()
    if replayed:
        response.status_code = 200
    return JobResponse(job=job, replayed=replayed)


@router.get("/v1/ingestion/jobs/{job_id}")
def ingestion_job(
    job_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("query"))],
) -> JobResponse:
    """The audited job row — status is a named state, never fabricated."""
    return JobResponse(job=GetIngestionJob(uow).execute(job_id=job_id))
