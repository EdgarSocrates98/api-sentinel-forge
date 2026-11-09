"""`/v1/investigations/{id}` — the persisted context pack, verbatim."""

from typing import Annotated

from fastapi import APIRouter, Depends

from api_sentinel.api.deps import get_uow
from api_sentinel.api.schemas import InvestigationResponse
from api_sentinel.application.ports import UnitOfWork
from api_sentinel.application.use_cases import GetInvestigation

router = APIRouter(tags=["investigations"])


@router.get("/v1/investigations/{investigation_id}")
def investigation(
    investigation_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> InvestigationResponse:
    """The stored investigation: bounded pack + honest unknowns."""
    row = GetInvestigation(uow).execute(investigation_id=investigation_id)
    pack = row.context_pack if isinstance(row.context_pack, dict) else {}
    unknowns = [str(u) for u in pack.get("unknowns", [])]
    evidence_refs = [
        str(r) for r in row.result.get("evidence_refs", [])
    ] if isinstance(row.result, dict) else []
    return InvestigationResponse(
        investigation=row,
        unknowns=unknowns,
        evidence_refs=evidence_refs,
    )
