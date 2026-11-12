"""`/v1/evidence/{id}` — authorized evidence *metadata*, audited access."""

from typing import Annotated

from fastapi import APIRouter, Depends

from api_sentinel.api.deps import (
    ApiContext,
    get_ctx,
    get_uow,
    require_scope,
)
from api_sentinel.api.schemas import EvidenceResponse
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.application.use_cases import GetEvidenceMetadata

router = APIRouter(tags=["evidence"])


@router.get("/v1/evidence/{evidence_id}")
def evidence_metadata(
    evidence_id: str,
    ctx: Annotated[ApiContext, Depends(get_ctx)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor: Annotated[str, Depends(require_scope("query"))],
) -> EvidenceResponse:
    """Artifact metadata only — blob content is never served over the API;
    the read itself is journaled (docs/07 evidence-access audit)."""
    artifact = GetEvidenceMetadata(uow, ctx.clock, ctx.ids).execute(
        evidence_id=evidence_id, actor=actor
    )
    return EvidenceResponse(evidence=artifact, evidence_refs=[artifact.evidence_id])
