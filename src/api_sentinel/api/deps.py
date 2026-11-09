"""Dependency injection + the Cycle-6 authorization placeholder.

Workspace plumbing: every request gets a fresh `SqliteUnitOfWork` over the
resolved workspace (`API_SENTINEL_HOME` or `--workspace` at `serve` time),
the injected clock/id factory/redactor, and the Atlas structural port when —
and only when — a configured export loaded successfully.

Authorization (LAB-016 groundwork; multi-user authz is Cycle 9, docs/07):
the effective role comes from the `X-Sentinel-Role` header or the
workspace-configured default, mapped to the docs/07 capability scopes.
A denied request raises `ForbiddenError` *before* any resource lookup — a
403 never leaks whether the target exists. Denials are audit-recorded
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

    def uow(self) -> UnitOfWork:
        """A fresh unit of work per call — connections never cross requests."""
        return SqliteUnitOfWork(self.workspace.db_path)


# ---------------------------------------------------------------------------
# Capability scopes (docs/07 permission model — LAB-016 placeholder).
# The role header is an explicit *placeholder*: single-user local operator
# today, a real principal/token exchange lands in Cycle 9 — this seam is the
# enforcement point, and denial happens before resource lookup.

ROLE_SCOPES: dict[str, frozenset[str]] = {
    "viewer": frozenset({"query"}),
    "analyst": frozenset({"query", "investigate"}),
    "operator": frozenset(
        {"query", "import", "investigate", "manage_sources"}
    ),
    "admin": frozenset(
        {"query", "import", "investigate", "manage_sources", "approve_changes"}
    ),
}


def get_ctx(request: Request) -> ApiContext:
    ctx: ApiContext = request.app.state.ctx
    return ctx


def get_uow(ctx: Annotated[ApiContext, Depends(get_ctx)]) -> UnitOfWork:
    return ctx.uow()


def get_actor(request: Request) -> str:
    """The caller's audit identity — a header today, a principal in Cycle 9."""
    return request.headers.get("x-sentinel-actor", "api").strip() or "api"


def _audit_denied(
    ctx: ApiContext, *, actor: str, role: str, scope: str, path: str
) -> None:
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


def require_scope(scope: str) -> Callable[..., str]:
    """FastAPI dependency: gate a route on a docs/07 capability scope.

    Returns the actor on success; raises `ForbiddenError` before any lookup
    on denial — existence of the target is never revealed.
    """

    def dependency(
        request: Request,
        ctx: Annotated[ApiContext, Depends(get_ctx)],
        actor: Annotated[str, Depends(get_actor)],
    ) -> str:
        raw = request.headers.get("x-sentinel-role", "").strip().lower()
        role = raw or ctx.default_role
        scopes = ROLE_SCOPES.get(role)
        if scopes is None or scope not in scopes:
            _audit_denied(
                ctx, actor=actor, role=role, scope=scope,
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
            "mutation executor saturated — retry when a running "
            "import/investigation finishes",
            details={"max_concurrent_jobs": ctx.max_concurrent_jobs},
        )
    return ctx.jobs_limiter.release
