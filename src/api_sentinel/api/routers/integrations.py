"""`/v1/integrations/status` — every sibling Forge's honest state."""

from typing import Annotated

from fastapi import APIRouter, Depends

from api_sentinel.api.deps import ApiContext, get_ctx, require_scope
from api_sentinel.api.schemas import IntegrationStatusResponse
from api_sentinel.integrations.status import integration_status

router = APIRouter(tags=["integrations"])


@router.get("/v1/integrations/status")
def integrations_status(
    ctx: Annotated[ApiContext, Depends(get_ctx)],
    actor: Annotated[str, Depends(require_scope("query"))],
) -> IntegrationStatusResponse:
    """`available | unverified | unavailable` per Forge — a named state with
    a reason, never a simulated specialist result."""
    integrations = integration_status(atlas_manifest=ctx.atlas_manifest)
    unknowns = [f"{i['forge']}:{i['state']}" for i in integrations if i.get("state") != "available"]
    return IntegrationStatusResponse(integrations=integrations, unknowns=unknowns)
