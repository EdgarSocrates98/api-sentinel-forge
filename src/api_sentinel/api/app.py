"""FastAPI factory — the thin /v1 surface (docs/04, ADR-011).

Localhost-bound at `serve` time (docs/07); this module only wires routers,
dependencies and the typed `problem+json` error contract — business logic
stays in application use cases. There is deliberately no OpenAPI/docs UI:
the surface is the documented route table, nothing more.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api_sentinel import __version__
from api_sentinel.api import routers
from api_sentinel.api.deps import ApiContext
from api_sentinel.api.schemas import ProblemDetails
from api_sentinel.domain import (
    CapabilityUnavailableError,
    Clock,
    ConcurrencyLimitError,
    ConflictError,
    ContractError,
    ForbiddenError,
    IdFactory,
    NotFoundError,
    SentinelError,
    SystemClock,
    UlidIdFactory,
    UnauthorizedError,
    ValidationError,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.security.auth import ApiAuth, resolve_auth
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)
from api_sentinel.integrations.service import load_structural
from api_sentinel.workspace import (
    Workspace,
    ensure_workspace,
    load_config,
    resolve_workspace,
)

# Sentinel error family → HTTP status (typed problem+json, docs/04).
_ERROR_STATUS: list[tuple[type[SentinelError], int]] = [
    (UnauthorizedError, 401),
    (NotFoundError, 404),
    (ValidationError, 422),
    (ForbiddenError, 403),
    (ConflictError, 409),
    (ConcurrencyLimitError, 503),
    (CapabilityUnavailableError, 503),
    (IntegrationUnavailable, 503),
    (IntegrationRefused, 409),
    (ContractError, 422),
]

#: CORS allow-headers — the credential channels plus content type.
_CORS_ALLOW_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-Sentinel-Principal",
    "X-Sentinel-Role",
    "X-Sentinel-Workspace",
    "X-Sentinel-Timestamp",
    "X-Sentinel-Nonce",
    "X-Sentinel-Signature",
]


def _problem(
    *, status: int, code: str, detail: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    body = ProblemDetails(
        type=f"urn:api-sentinel:{code}",
        title=code.removeprefix("sentinel.").replace("_", " "),
        status=status,
        detail=detail,
        code=code,
        details=details or {},
    )
    return JSONResponse(
        status_code=status,
        content=body.model_dump(mode="json"),
        media_type="application/problem+json",
        headers=(
            {"WWW-Authenticate": 'Bearer realm="api-sentinel"'}
            if status == 401
            else None
        ),
    )


def _atlas_manifest(config: dict[str, Any], ws: Workspace) -> Path | None:
    integrations = config.get("integrations")
    if not isinstance(integrations, dict):
        return None
    atlas = integrations.get("atlas")
    if not isinstance(atlas, dict) or not atlas.get("export"):
        return None
    path = Path(str(atlas["export"]))
    return path if path.is_absolute() else ws.root / path


def _cors_origins(config: dict[str, Any]) -> list[str]:
    """Explicit CORS allow-list — default is *deny cross-origin* (no
    middleware). `api.cors_origins` opts in; `*` is honored but never
    paired with credentials (they are off, always)."""
    api_raw = config.get("api")
    api_cfg: dict[str, Any] = api_raw if isinstance(api_raw, dict) else {}
    raw = api_cfg.get("cors_origins")
    if not isinstance(raw, list):
        return []
    return sorted({str(o).strip() for o in raw if str(o).strip()})


def _build_context(
    ws: Workspace, *, clock: Clock, ids: IdFactory, auth: ApiAuth | None
) -> ApiContext:
    """Resolve collaborators once — an unusable Atlas export is a named
    `structural_reason`, never a fabricated structural graph."""
    config: dict[str, Any] = {}
    if ws.config_path.exists():
        try:
            config = load_config(ws)
        except SentinelError:
            config = {}
    manifest = _atlas_manifest(config, ws)
    structural = None
    structural_reason = "no atlas export configured — structural context unavailable"
    if manifest is not None:
        try:
            structural = load_structural(config, ws.root)
            if structural is not None:
                structural_reason = ""
        except SentinelError as exc:
            structural_reason = getattr(exc, "reason", exc.message)
    api_raw = config.get("api")
    api_cfg: dict[str, Any] = api_raw if isinstance(api_raw, dict) else {}
    resolved_auth = auth
    if resolved_auth is None:
        # The served path always resolves a credential — in-process mode
        # (`auth=None` on ApiContext) is only reachable by direct
        # ApiContext injection, never through `serve`.
        resolved_auth, _ = resolve_auth(ws, config)
    return ApiContext(
        workspace=ws,
        config=config,
        clock=clock,
        ids=ids,
        redactor=lambda text: redact(text).text,
        structural=structural,
        structural_reason=structural_reason,
        atlas_manifest=manifest,
        default_role=str(api_cfg.get("default_role", "operator")),
        max_concurrent_jobs=int(api_cfg.get("max_concurrent_jobs", 2)),
        auth=resolved_auth,
        cors_origins=_cors_origins(config),
    )


def create_app(
    *,
    workspace: Path | None = None,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
    context: ApiContext | None = None,
    auth: ApiAuth | None = None,
) -> FastAPI:
    """Build the /v1 app over a resolved workspace.

    `context` may inject a fully custom `ApiContext` (tests); otherwise one
    is built from `workspace` (which falls back to `API_SENTINEL_HOME` then
    `./.api-sentinel`). `auth` lets `serve` inject the credential it
    already resolved (env/stdin/file); absent it, the workspace token
    file is resolved here — serving never runs credential-less.
    """
    if context is not None:
        ctx = context
    else:
        ws = resolve_workspace(explicit=workspace)
        ensure_workspace(ws)
        ctx = _build_context(
            ws,
            clock=clock or SystemClock(),
            ids=ids or UlidIdFactory(),
            auth=auth,
        )

    app = FastAPI(
        title="api-sentinel",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.ctx = ctx

    if ctx.cors_origins:
        # Explicit opt-in only; credentials stay off — a wildcard origin
        # is therefore never a credentialed wildcard.
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=ctx.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=_CORS_ALLOW_HEADERS,
        )

    @app.exception_handler(SentinelError)
    async def _sentinel_error(
        _request: Request, exc: SentinelError
    ) -> JSONResponse:
        status = next(
            (s for cls, s in _ERROR_STATUS if isinstance(exc, cls)), 500
        )
        return _problem(
            status=status, code=exc.code,
            detail=exc.message, details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def _request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _problem(
            status=422,
            code=ValidationError.code,
            detail="request validation failed",
            details={"errors": [str(e) for e in exc.errors()]},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = (
            NotFoundError.code
            if exc.status_code == 404
            else "sentinel.http_error"
        )
        return _problem(
            status=exc.status_code, code=code, detail=str(exc.detail)
        )

    for module in (
        routers.health,
        routers.services,
        routers.ingestion,
        routers.anomalies,
        routers.incidents,
        routers.investigations,
        routers.evidence,
        routers.integrations,
    ):
        app.include_router(module.router)

    return app
