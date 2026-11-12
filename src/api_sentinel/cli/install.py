"""Guided install — profile + scope selection; headless via --yes."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any, cast

import typer

from api_sentinel import __version__
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.workspace import Scope, ensure_workspace, resolve_workspace

PROFILES = ("local", "aws", "opentelemetry")
SCOPES = ("project", "user")
FORGE_ID = "api-sentinel-forge"


def install_command(
    profile: Annotated[
        str, typer.Option("--profile", help="local | aws | opentelemetry")
    ] = "local",
    scope: Annotated[str, typer.Option("--scope", help="project | user")] = "project",
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Non-interactive")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the install plan; write nothing")
    ] = False,
) -> None:
    """Initialize an API Sentinel workspace (guided or headless)."""
    if profile not in PROFILES:
        typer.echo(f"error: unknown profile '{profile}' (expected one of {PROFILES})", err=True)
        raise typer.Exit(2)
    if scope not in SCOPES:
        typer.echo(f"error: unknown scope '{scope}' (expected one of {SCOPES})", err=True)
        raise typer.Exit(2)
    ws = resolve_workspace(
        scope=cast(Scope, scope), explicit=Path(workspace) if workspace is not None else None
    )
    if dry_run:
        plan = {
            "dry_run": True,
            "workspace": str(ws.root),
            "profile": profile,
            "scope": scope,
            "portal": _portal_present(),
            "actions": [
                "ensure_workspace",
                "apply_migrations",
                "ensure_local_token",
                *(["register_forge_context"] if _context_root(ws) else []),
            ],
        }
        typer.echo(
            json.dumps(plan)
            if as_json
            else f"[dry-run] would initialize workspace {ws.root} "
            f"(profile={profile}, scope={scope})"
        )
        return
    if not yes and sys.stdin.isatty():
        profile = typer.prompt("Profile", default=profile)
        scope = typer.prompt("Scope", default=scope)
        ws = resolve_workspace(
            scope=cast(Scope, scope),
            explicit=Path(workspace) if workspace is not None else None,
        )
    ensure_workspace(ws, profile=profile)
    with SqliteUnitOfWork(ws.db_path):  # applies migrations
        pass
    from api_sentinel.infrastructure.security.auth import ensure_local_token

    resolved = ensure_local_token(ws)
    context = _register_context(ws, profile)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "workspace": str(ws.root),
                    "profile": profile,
                    "scope": scope,
                    "portal": _portal_present(),
                    "token_created": resolved.created,
                    "token_path": str(resolved.path) if resolved.path else None,
                    "context_registry": context,
                }
            )
        )
        if context["status"] == "blocked":
            raise typer.Exit(1)
        return
    typer.echo(f"Workspace ready: {ws.root}")
    typer.echo(f"Profile: {profile}  Scope: {scope}")
    if context["status"] == "blocked":
        typer.echo(f"Context registry: BLOCKED — {context['detail']}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Context registry: {context['status']}")
    if resolved.created:
        typer.echo(
            f"API token generated: {resolved.path}\n"
            f"  Authorization: Bearer {resolved.token}\n"
            "shown once — read the token file to retrieve it later"
        )
    if _portal_present():
        typer.echo("Forge Portal detected")
    else:
        typer.echo("Forge Portal not found — API Operations activates when available")


def _portal_present() -> bool:
    return shutil.which("forge-portal") is not None


def update_command(
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    scope: Annotated[str, typer.Option("--scope", help="project | user")] = "project",
    as_json: Annotated[bool, typer.Option("--json")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Re-apply migrations and refresh the local token (idempotent)."""
    ws = resolve_workspace(
        scope=cast(Scope, scope), explicit=Path(workspace) if workspace is not None else None
    )
    if not (ws.root / ws.db_path.name).exists() and not ws.root.exists():
        typer.echo(
            json.dumps({"updated": False, "reason": "workspace_not_installed"})
            if as_json
            else f"Workspace not installed at {ws.root} — run `api-sentinel install`",
            err=not as_json,
        )
        raise typer.Exit(2)
    if dry_run:
        typer.echo(
            json.dumps(
                {
                    "dry_run": True,
                    "workspace": str(ws.root),
                    "actions": ["apply_migrations", "ensure_local_token"],
                }
            )
            if as_json
            else f"[dry-run] would update workspace {ws.root}"
        )
        return
    with SqliteUnitOfWork(ws.db_path):
        pass
    from api_sentinel.infrastructure.security.auth import ensure_local_token

    resolved = ensure_local_token(ws)
    # Converge the shared context layer when it already exists — update
    # never creates `.forge/context/` on a pristine root (same rule as
    # the install siblings' repair).
    context = _repair_context(ws)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "updated": True,
                    "workspace": str(ws.root),
                    "token_created": resolved.created,
                    "context_registry": context,
                }
            )
        )
        return
    typer.echo(f"Workspace updated: {ws.root}")
    if context is not None:
        typer.echo(f"Context registry: {context['status']}")


def uninstall_command(
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    scope: Annotated[str, typer.Option("--scope", help="project | user")] = "project",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Non-interactive")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Remove the workspace directory. Never silent — requires --yes or a TTY confirm."""
    ws = resolve_workspace(
        scope=cast(Scope, scope), explicit=Path(workspace) if workspace is not None else None
    )
    if not ws.root.exists():
        typer.echo(
            json.dumps({"uninstalled": False, "reason": "workspace_absent"})
            if as_json
            else f"Nothing to remove at {ws.root}"
        )
        return
    if dry_run:
        typer.echo(
            json.dumps({"dry_run": True, "would_remove": str(ws.root)})
            if as_json
            else f"[dry-run] would remove {ws.root}"
        )
        return
    if not yes:
        if not sys.stdin.isatty():
            typer.echo("refused: uninstall requires --yes in non-interactive mode", err=True)
            raise typer.Exit(2)
        typer.confirm(f"Remove workspace {ws.root} (data loss)?", abort=True)
    try:
        shutil.rmtree(ws.root)
    except OSError as exc:
        typer.echo(
            json.dumps({"uninstalled": False, "reason": "locked", "detail": str(exc)})
            if as_json
            else f"refused: cannot remove {ws.root} — {exc} "
            "(close processes holding it; on Windows do not run from inside it)",
            err=not as_json,
        )
        raise typer.Exit(1) from exc
    context = _unregister_context(ws)
    if as_json:
        typer.echo(
            json.dumps({"uninstalled": True, "removed": str(ws.root), "context_registry": context})
        )
        if context["status"] == "blocked":
            raise typer.Exit(1)
        return
    typer.echo(f"Removed workspace {ws.root}")
    if context["status"] == "blocked":
        typer.echo(f"Context registry: BLOCKED — {context['detail']}", err=True)
        raise typer.Exit(1)


# --------------------------------------------------------------------------
# forge-context/v1 wiring (install-2.0 — same registry the api-forge/
# spark-aws/platform/data-sentinel/doctor siblings write). `.forge/context/`
# is the workspace-level registry shared between forges — a different layer
# from the sentinel workspace state (`.api-sentinel/`). This forge only ever
# registers/unregisters its own `forge_id`.
#
# Anchor: a project-scope install creates `.api-sentinel/` *inside* the
# consumer's project, so the context root is `ws.root.parent`. `user` scope
# resolves to `~/.api-sentinel` — account level; workspace context is never
# written there.
# --------------------------------------------------------------------------

_PKG_DIR = Path(__file__).resolve().parents[1]
_REPO_DIR = _PKG_DIR.parent.parent  # src/api_sentinel -> repo root

_CONSUMER_DOCS: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md", "AGENT_PROTOCOL.md")

_SENTINEL_DOMAINS: tuple[str, ...] = (
    "api-reliability",
    "runtime-observation",
    "slo-assessment",
    "anomaly-detection",
    "incident-investigation",
    "deployment-correlation",
)


def _context_root(ws: Any) -> Path | None:
    """Where `.forge/context/` belongs for this workspace — the dir that
    owns the sentinel data dir — or None for account-scope installs."""
    if ws.scope != "project":
        return None
    parent = ws.root.parent
    if parent == ws.root or not str(parent):  # filesystem root guard
        return None
    return parent


def _bundled_json(name: str) -> dict[str, Any] | None:
    import json as _json

    for cand in (_PKG_DIR / name, _REPO_DIR / name):
        try:
            data = _json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _context_manifest(ws: Any, profile: str) -> dict[str, Any]:
    """The ``forge/context/v1`` manifest built from the real sources:
    ``forge.json`` / ``forge.agentic.json`` (repo root) and the declared
    capability surface."""
    from api_sentinel import _forgecontext as fctx

    return fctx.build_manifest(
        forge_id=FORGE_ID,
        name="API Sentinel Forge",
        version=__version__,
        cli="api-sentinel",
        package="api_sentinel",
        distribution="api-sentinel-forge",
        python_spec=">=3.11",
        scope=ws.scope,
        # `installed.profile` is the installkit disclosure tier
        # (minimal|recommended|full) — the sentinel installs one tier;
        # its runtime profile (local|aws|opentelemetry) is recorded
        # under `components` instead.
        profile="recommended",
        hosts=(),
        install_modes=SCOPES,
        forge_json=_bundled_json("forge.json"),
        agentic=_bundled_json("forge.agentic.json"),
        kind="diagnostic-provider",
        domains=_SENTINEL_DOMAINS,
        protocols=(
            {"id": "forge/v1", "version": "1"},
            {"id": "forge/context/v1", "version": "1"},
            {"id": "forge/context-registry/v1", "version": "1"},
        ),
        discovery_commands=(
            {
                "id": "status",
                "argv": ["api-sentinel", "status", "--json"],
                "mode": "diagnostic-only",
                "read_only": True,
                "description": "workspace state: services, sources, storage health",
            },
            {
                "id": "capabilities",
                "argv": ["api-sentinel", "capabilities", "--json"],
                "mode": "diagnostic-only",
                "read_only": True,
                "description": "declared capability surface and availability",
            },
            {
                "id": "doctor",
                "argv": ["api-sentinel", "doctor", "--json"],
                "mode": "diagnostic-only",
                "read_only": True,
                "description": "environment and installation health",
            },
        ),
        mcp={
            "server_name": "api-sentinel",
            "command": ["api-sentinel", "mcp", "serve"],
        },
        resources={
            "sentinel-workspace": {
                "kind": "referenced",
                "location": ".api-sentinel",
                "optional": True,
            },
        },
        mutating_verbs=("install", "uninstall", "update"),
        requires_approval=("install", "uninstall"),
        offline_available=True,
        health={
            "status": "healthy",
            "checks": [
                {"id": "workspace", "status": "PASS", "detail": "workspace initialized"},
            ],
        },
        components={"runtime_profile": profile},
        recorded_by="api-sentinel",
    )


def _register_context(ws: Any, profile: str) -> dict[str, Any]:
    """Write `.forge/context/` — manifest + registry entry + ROUTING.md +
    consumer docs — in ONE transaction. Returns the honest outcome for the
    payload: registered / skipped (account scope) / blocked (named refusal).
    """
    from api_sentinel import _forgecontext as fctx

    root = _context_root(ws)
    if root is None:
        return {"status": "skipped", "detail": "account scope — no workspace context"}
    try:
        out = fctx.install_transaction(root, _context_manifest(ws, profile), owner=FORGE_ID)
        docs = out["docs"]
        return {
            "status": "registered",
            "detail": f".forge/context {out['status']} "
            f"({len(out['written'])} writes; docs {docs['status']})",
            "root": str(root),
        }
    except fctx.ContextError as exc:
        return {"status": "blocked", "detail": f"{exc.kind}: {exc.detail}"}


def _unregister_context(ws: Any) -> dict[str, Any]:
    """Remove this forge's manifest/entry/`forge:*` blocks in ONE
    transaction — only when residue exists (the transaction acquires its
    own lock and would create `.forge/context/` on a pristine root)."""
    from api_sentinel import _forgecontext as fctx

    root = _context_root(ws)
    if root is None or not _docs_leftovers(root):
        return {"status": "skipped", "detail": "no context residue"}
    try:
        out = fctx.uninstall_transaction(root, FORGE_ID, owner=FORGE_ID)
        return {
            "status": "unregistered",
            "detail": f".forge/context {out['status']} (docs {out['docs']['status']})",
        }
    except fctx.ContextError as exc:
        return {"status": "blocked", "detail": f"{exc.kind}: {exc.detail}"}


def _repair_context(ws: Any) -> dict[str, Any] | None:
    """Converge an existing `.forge/context/` — journal recovery, stale
    locks, registry+ROUTING.md rebuild and `forge:*` doc re-assert. None
    when the layer was never installed (no residue created)."""
    from api_sentinel import _forgecontext as fctx

    root = _context_root(ws)
    if root is None or not (root / fctx.CONTEXT_DIR).exists():
        return None
    try:
        out_ctx = fctx.repair_context(root)
        out_docs = fctx.repair_docs(root)
        return {
            "status": "repaired",
            "detail": f"context {out_ctx['status']} "
            f"({len(out_ctx['written'])} writes); "
            f"docs {out_docs['status']}",
        }
    except fctx.ContextError as exc:
        return {"status": "blocked", "detail": f"{exc.kind}: {exc.detail}"}


def _docs_leftovers(root: Path) -> bool:
    """True when the target still carries forge_context residue — a
    lock-free read; only a hit justifies the write path (we never create
    `.forge/context/` on a pristine root)."""
    from api_sentinel import _forgecontext as fctx

    if (root / fctx.CONTEXT_DIR).exists():
        return True
    needles = (
        f"forge:{FORGE_ID}:",
        f"{fctx.DOC_TAG_ECOSYSTEM}:",
        fctx.PROTOCOL_SENTINEL,
    )
    for name in _CONSUMER_DOCS:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(n in text for n in needles):
            return True
    return False
