"""`/v1/health` + `/v1/capabilities` — the Sentinel's own honest state."""

from typing import Annotated

from fastapi import APIRouter, Depends

from api_sentinel import __version__
from api_sentinel.api.deps import ApiContext, get_ctx
from api_sentinel.api.schemas import (
    CapabilitiesResponse,
    CapabilityView,
    HealthResponse,
)
from api_sentinel.capabilities import list_capabilities

router = APIRouter(tags=["sentinel"])


@router.get("/v1/health")
def health(ctx: Annotated[ApiContext, Depends(get_ctx)]) -> HealthResponse:
    """Process/workspace self-report — `PARTIAL`, never fiction, when the
    database is absent; a global "health 87%" score does not exist."""
    db_present = ctx.workspace.db_path.is_file()
    unknowns = [] if db_present else ["database_absent"]
    return HealthResponse(
        state="AVAILABLE" if db_present else "PARTIAL",
        version=__version__,
        workspace_initialized=ctx.workspace.root.is_dir(),
        database="present" if db_present else "absent",
        capabilities={
            "available": sum(
                1 for c in list_capabilities() if c.status.value == "available"
            ),
            "declared": sum(
                1 for c in list_capabilities() if c.status.value == "declared"
            ),
        },
        unknowns=unknowns,
    )


@router.get("/v1/capabilities")
def capabilities(
    ctx: Annotated[ApiContext, Depends(get_ctx)],
) -> CapabilitiesResponse:
    """The real capability surface — `available` vs `declared` states."""
    return CapabilitiesResponse(
        capabilities=[
            CapabilityView(
                capability_id=c.capability_id,
                status=c.status.value,
                since=c.since,
                notes=c.notes,
            )
            for c in list_capabilities()
        ]
    )
