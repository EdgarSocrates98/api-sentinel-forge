"""Dependency injection + Cycle-10 authentication/authorization.

Workspace plumbing: every request gets a fresh `SqliteUnitOfWork` over the
resolved workspace (`API_SENTINEL_HOME` or `--workspace` at `serve` time),
the injected clock/id factory/redactor, and the Atlas structural port when —
and only when — a configured export loaded successfully.

Authentication (docs/07; replaces the Cycle-6 `X-Sentinel-Role`
placeholder — C10-F007):

- `ctx.auth` set → real credentials required on every `/v1/*` route except
  `/v1/health`. A `Authorization: Bearer` token grants the workspace
  `token_role`; an `X-Sentinel-Principal` is honored *only* with a valid
  `X-Sentinel-Signature` (HMAC over principal+role+workspace+ts+nonce,
  ±300 s skew, single-use nonce). Self-declared role headers without
  valid credentials are rejected `401`, never trusted.
- `ctx.auth is None` → *in-process mode* (TestClient/tests): the
  documented placeholder applies — `X-Sentinel-Role` or the workspace
  default maps to scopes. `serve` always resolves a token, so this mode
  is only reachable by injecting `ApiContext` directly.

Authorization: the authenticated role maps to docs/07 capability scopes.
A denied request raises `ForbiddenError` *before* any resource lookup —
a 403 never leaks whether the target exists. Denials are audit-recorded
best-effort; the denial itself is the contract.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request

from api_sentinel.application.ports import UnitOfWork
from api_sentinel.domain import (
    AuditEvent,
    Clock,
    ForbiddenError,
    IdFactory,
    SentinelError,
    UnauthorizedError,
)
from api_sentinel.infrastructure.security.auth import (
    HDR_NONCE,
    HDR_PRINCIPAL,
    HDR_ROLE,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    HDR_WORKSPACE,
    ApiAuth,
    _refusal,
)
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.integrations.atlas.port import StructuralContextPort
from api_sentinel.workspace import Workspace


@dataclass
class ApiContext:
    """Per-app injected collaborators — built once at `create_app`."""

    workspace: Workspace
    config: dict[str, Any]
    clock: Clock
    ids: IdFactory
    redactor: Callable[[str], str]
    structural: StructuralContextPort | None
    structural_reason: str
    atlas_manifest: Path | None
    default_role: str = "operator"
    max_concurrent_jobs: int = 2
    jobs_limiter: threading.BoundedSemaphore = field(
        default_factory=lambda: threading.BoundedSemaphore(2)
    )
    auth: ApiAuth | None = None  # None → in-process mode (tests only)
    cors_origins: list[str] = field(default_factory=list)

    def uow(self) -> UnitOfWork:
        """A fresh unit of work per call — connections never cross requests."""
        return SqliteUnitOfWork(self.workspace.db_path)


# ---------------------------------------------------------------------------
# Capability scopes (docs/07 permission model).
# Denial happens before resource lookup; authentication happens before the
# scope check — the enforcement point lives in `require_scope`.

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "viewer": frozenset({"query"}),
    "analyst": frozenset({"query", "investigate"}),
    "operator": frozenset({"query", "import", "investigate", "manage_sources"}),
    "admin": frozenset({"query", "import", "investigate", "manage_sources", "approve_changes"}),
}

_KNOWN_ROLES = frozenset(ROLE_SCOPES)


def get_ctx(request: Request) -> ApiContext:
    ctx: ApiContext = request.app.state.ctx
    return ctx


def get_uow(ctx: Annotated[ApiContext, Depends(get_ctx)]) -> UnitOfWork:
    return ctx.uow()


def get_actor(request: Request) -> str:
    """The caller's audit identity — the authenticated principal when auth
    is on, else the documented `X-Sentinel-Actor` in-process fallback."""
    return request.headers.get("x-sentinel-actor", "api").strip() or "api"


def _authenticate(request: Request, ctx: ApiContext) -> tuple[str, str]:
    """Resolve (actor, role) for this request or raise 401.

    Forwarded/proxy headers (`X-Forwarded-*`, `X-Real-IP`) are never
    consulted — identity comes only from credentials this server verifies.
    """
    auth = ctx.auth
    if auth is None or not auth.enabled:
        # In-process mode — the documented Cycle-6 placeholder, reachable
        # only by direct ApiContext injection (tests/TestClient).
        raw = request.headers.get(HDR_ROLE, "").strip().lower()
        return get_actor(request), raw or ctx.default_role

    now = ctx.clock.now()
    authorization = request.headers.get("authorization", "")
    principal = request.headers.get(HDR_PRINCIPAL, "").strip()

    if authorization:
        scheme, _, presented = authorization.partition(" ")
        if scheme.strip().lower() != "bearer" or not presented.strip():
            raise _refusal("bad_authorization_scheme")
        auth.verify_bearer(presented.strip(), now)
        return "token:local", auth.token_role

    if principal:
        role = auth.verify_signature(
            principal=principal,
            role=request.headers.get(HDR_ROLE, "").strip().lower(),
            workspace=request.headers.get(HDR_WORKSPACE, "").strip(),
            timestamp=request.headers.get(HDR_TIMESTAMP, "").strip(),
            nonce=request.headers.get(HDR_NONCE, "").strip(),
            signature=request.headers.get(HDR_SIGNATURE, "").strip(),
            now=now,
            known_roles=_KNOWN_ROLES,
        )
        return f"principal:{principal}", role

    # A self-declared X-Sentinel-Role with no credential is ignored —
    # never mapped to scopes.
    raise _refusal("missing_credentials")


def _audit_denied(ctx: ApiContext, *, actor: str, role: str, scope: str, path: str) -> None:
    """Denials are auditable too — best-effort: an audit failure never
    changes the denial outcome."""
    try:
        with ctx.uow() as uow:
            uow.audit.append(
                AuditEvent(
                    audit_id=ctx.ids.new("aud"),
                    at=ctx.clock.now(),
                    actor=actor,
                    action="api.scope.denied",
                    target_type="api_scope",
                    target_id=scope,
                    details={"role": role, "path": path},
                )
            )
            uow.commit()
    except SentinelError:
        pass


def _audit_authn_failure(ctx: ApiContext, *, reason: str, path: str) -> None:
    """401s are journaled by reason — credential material never lands in
    the audit row."""
    try:
        with ctx.uow() as uow:
            uow.audit.append(
                AuditEvent(
                    audit_id=ctx.ids.new("aud"),
                    at=ctx.clock.now(),
                    actor="api",
                    action="api.auth.denied",
                    target_type="api_auth",
                    target_id=reason,
                    details={"path": path},
                )
            )
            uow.commit()
    except SentinelError:
        pass


def require_scope(scope: str) -> Callable[..., str]:
    """FastAPI dependency: authenticate, then gate a route on a docs/07
    capability scope.

    Returns the actor on success; raises `UnauthorizedError` on missing/
    bad credentials and `ForbiddenError` on insufficient scope — always
    before any lookup, so existence of the target is never revealed.
    """

    def dependency(
        request: Request,
        ctx: Annotated[ApiContext, Depends(get_ctx)],
    ) -> str:
        try:
            actor, role = _authenticate(request, ctx)
        except UnauthorizedError as exc:
            _audit_authn_failure(
                ctx,
                reason=str(exc.details.get("reason", "unauthorized")),
                path=str(request.url.path),
            )
            raise
        scopes = ROLE_SCOPES.get(role)
        if scopes is None or scope not in scopes:
            _audit_denied(
                ctx,
                actor=actor,
                role=role,
                scope=scope,
                path=str(request.url.path),
            )
            raise ForbiddenError(
                f"scope denied: {scope} requires one of "
                f"{sorted(r for r, s in ROLE_SCOPES.items() if scope in s)}",
                details={"scope": scope, "role": role},
            )
        return actor

    return dependency


def acquire_job_slot(ctx: ApiContext) -> Callable[[], None]:
    """Bounded concurrent mutations — a saturated executor is a named
    refusal (`sentinel.concurrency_limit` → 503), never a silent queue."""
    from api_sentinel.domain import ConcurrencyLimitError

    if not ctx.jobs_limiter.acquire(blocking=False):
        raise ConcurrencyLimitError(
            "mutation executor saturated — retry when a running import/investigation finishes",
            details={"max_concurrent_jobs": ctx.max_concurrent_jobs},
        )
    return ctx.jobs_limiter.release
