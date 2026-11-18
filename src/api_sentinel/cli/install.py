"""Install lifecycle — service-forge bootstrap (install-2.1, Cycle 16 wave 11).

Two profile families stay deliberately distinct:

- **install tier** (``--profile``): ``minimal | recommended | full`` — the
  installkit disclosure tier. This forge ships a single service bundle
  (state dir + migrations + local token + context registration), so all
  tiers install the same artifacts; the chosen tier is recorded, never
  expanded silently into runtime meaning.
- **runtime profile** (``--runtime-profile``): ``local | aws |
  opentelemetry`` — the functional profile persisted in ``config.yaml``
  (telemetry/integration posture). ``full`` is never a runtime profile.

Backwards compatibility: the pre-2.1 ``--profile`` accepted the runtime
values. They are still accepted — as a deprecated alias for
``--runtime-profile`` with a named warning — while unknown values stay
usage errors.

Flag family (delegation contract): ``--scope`` (project|workspace|user),
``--root`` (explicit target root — state dir is ``<root>/.api-sentinel``),
``--workspace`` (verbatim state dir, the flag every other command uses),
``--host`` (accepted, recorded NOT_APPLICABLE — a service forge writes
no host assets), ``--components`` (accepted, honestly recorded),
``--gateway-profile``/``--gateway`` (accepted + persisted, advisory —
the sentinel has no gateway mode), ``--yes``/``--dry-run``/``--json``
and ``--purge`` on uninstall.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated, Any, cast

import typer
import yaml

from api_sentinel import __version__, installation
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.workspace import (
    InstallTarget,
    Scope,
    ensure_workspace,
    resolve_install_target,
)

INSTALL_PROFILES = ("minimal", "recommended", "full")
RUNTIME_PROFILES = ("local", "aws", "opentelemetry")
SCOPES = ("project", "workspace", "user")
HOSTS = ("claude", "devin", "codex", "copilot")
GATEWAY_PROFILES = ("economy", "balanced", "deep")
FORGE_ID = "api-sentinel-forge"

install_app = typer.Typer(
    name="install",
    help="Install lifecycle — workspace bootstrap + install-2.1 ops.",
    invoke_without_command=True,
    no_args_is_help=False,
)

_JSON = Annotated[bool, typer.Option("--json", help="Emit the machine receipt on stdout")]
_YES = Annotated[bool, typer.Option("--yes", "-y", help="Non-interactive approval")]
_DRY = Annotated[bool, typer.Option("--dry-run", help="Print the plan; write nothing")]
_SCOPE = Annotated[str, typer.Option("--scope", help="project | workspace | user")]
_ROOT = Annotated[
    Path | None, typer.Option("--root", help="Explicit target root (state: <root>/.api-sentinel)")
]
_WS = Annotated[
    str | None, typer.Option("--workspace", help="Explicit state dir (verbatim path)")
]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _fail(msg: str, code: int = EXIT_USAGE) -> typer.Exit:
    typer.echo(f"error: {msg}", err=True)
    return typer.Exit(code)


def _resolve_target(scope: str, root: Path | None, workspace: str | None) -> InstallTarget:
    if scope not in SCOPES:
        raise _fail(f"unknown scope '{scope}' (expected one of {SCOPES})")
    try:
        return resolve_install_target(
            cast(Scope, scope),
            root=root,
            state_dir=Path(workspace) if workspace is not None else None,
        )
    except Exception as exc:
        # WorkspaceError (conflicting/unsafe targets) — a named usage refusal.
        raise _fail(str(exc)) from exc


def _resolve_profiles(
    profile: str | None,
    runtime_profile: str | None,
    warnings: list[str],
) -> tuple[str, str]:
    """(--profile tier, --runtime-profile) — the split contract.

    Deprecated path: a runtime value on ``--profile`` is honored as
    ``--runtime-profile`` with a named warning (never silently, never as
    an install tier). Unknown values on either flag are usage errors.
    """
    tier = "recommended"
    rp = runtime_profile
    if profile is not None:
        if profile in INSTALL_PROFILES:
            tier = profile
        elif profile in RUNTIME_PROFILES:
            warnings.append(
                f"--profile {profile!r} is a deprecated alias — pass "
                f"--runtime-profile {profile} (install tier defaults to 'recommended')"
            )
            if rp is None:
                rp = profile
        else:
            raise _fail(
                f"unknown profile '{profile}' (install tiers {INSTALL_PROFILES}; "
                f"runtime profiles {RUNTIME_PROFILES} via --runtime-profile)"
            )
    if rp is None:
        rp = "local"
    if rp not in RUNTIME_PROFILES:
        raise _fail(f"unknown runtime profile '{rp}' (expected one of {RUNTIME_PROFILES})")
    return tier, rp


def _resolve_hosts(host: str | None) -> tuple[str, ...]:
    """``all`` → every declared host; ``none``/empty → opt-out; csv →
    validated subset. The service install writes no host assets — the
    selection is recorded honestly, never an install action."""
    if host is None or host == "all":
        return ()
    if not host or host == "none":
        return ()
    names = tuple(dict.fromkeys(h.strip() for h in host.split(",") if h.strip()))
    unknown = [h for h in names if h not in HOSTS]
    if unknown:
        raise _fail(f"unknown host {unknown[0]!r} (expected {HOSTS}, 'all' or 'none')")
    return names


def _resolve_gateway(gateway_profile: str | None) -> str:
    gp = gateway_profile or "balanced"
    if gp not in GATEWAY_PROFILES:
        raise _fail(
            f"unknown gateway profile '{gp}' (expected one of {GATEWAY_PROFILES})"
        )
    return gp


def _require_tty_or_yes(verb: str, *, yes: bool, dry_run: bool) -> None:
    """Shared approval contract for mutating ops: non-interactive stdin
    without ``--yes``/``--dry-run`` is a named refusal (exit 2), never a
    silent write and never a blocked prompt."""
    if yes or dry_run:
        return
    if not sys.stdin.isatty():
        typer.echo(
            f"refused: {verb} in a non-interactive environment requires "
            "--yes or --dry-run",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)
    if not typer.confirm(f"{verb} — apply?", default=False):
        typer.echo("cancelled — nothing was written.")
        raise typer.Exit(EXIT_OK)


def _check(check_id: str, status: str, detail: str) -> dict[str, Any]:
    return {"id": check_id, "status": status, "detail": detail}


# --------------------------------------------------------------------------
# config.yaml — deliberate, journaled convergence (profile-change honesty)
# --------------------------------------------------------------------------


def _load_config_dict(ws: Any) -> dict[str, Any]:
    if not ws.config_path.exists():
        return {}
    data = yaml.safe_load(ws.config_path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _sync_config(
    ws: Any,
    *,
    previous: dict[str, Any],
    existed: bool,
    scope: str,
    runtime_profile: str,
    install_tier: str,
    gateway_profile: str,
) -> dict[str, Any]:
    """Converge ``config.yaml`` deliberately.

    ``previous``/``existed`` are the pre-``ensure_workspace`` snapshot so
    a fresh install reports ``created`` rather than a phantom update.
    A reinstall with a different runtime profile UPDATES the config and
    the receipt reports previous→effective — the requested value is
    never reported as effective without the write landing (the old code
    left ``local`` in the file while claiming ``aws``).
    """
    desired = dict(previous)
    desired.update(
        {
            "scope": scope,
            "profile": runtime_profile,
            "install_profile": install_tier,
            "gateway_profile": gateway_profile,
        }
    )
    changed = desired != previous
    if changed or not existed:
        tmp = ws.config_path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(desired, sort_keys=False), encoding="utf-8")
        tmp.replace(ws.config_path)
    return {
        "created": not existed,
        "updated": existed and changed,
        "previous_profile": previous.get("profile"),
        "effective_profile": runtime_profile,
        "previous_scope": previous.get("scope"),
        "effective_scope": scope,
    }


# --------------------------------------------------------------------------
# install
# --------------------------------------------------------------------------


@install_app.callback()
def install_cmd(
    ctx: typer.Context,
    scope: _SCOPE = "project",
    root: _ROOT = None,
    workspace: _WS = None,
    profile: Annotated[
        str | None, typer.Option("--profile", help="Install tier: minimal|recommended|full")
    ] = None,
    runtime_profile: Annotated[
        str | None,
        typer.Option("--runtime-profile", help="Runtime profile: local|aws|opentelemetry"),
    ] = None,
    host: Annotated[
        str | None,
        typer.Option(
            "--host",
            help="claude|devin|codex|copilot|all|none "
            "(recorded — a service forge writes no host assets)",
        ),
    ] = None,
    components: Annotated[
        str | None, typer.Option("--components", help="csv of install components")
    ] = None,
    gateway_profile: Annotated[
        str | None,
        typer.Option("--gateway-profile", "--gateway", help="economy|balanced|deep"),
    ] = None,
    yes: _YES = False,
    dry_run: _DRY = False,
    as_json: _JSON = False,
) -> None:
    """Initialize the API Sentinel workspace (guided or headless)."""
    if ctx.invoked_subcommand:
        return
    warnings: list[str] = []
    tier, rp = _resolve_profiles(profile, runtime_profile, warnings)
    hosts = _resolve_hosts(host)
    gp = _resolve_gateway(gateway_profile)
    comp = tuple(c.strip() for c in components.split(",") if c.strip()) if components else ()

    for w in warnings:
        typer.echo(f"warning: {w}", err=True)

    interactive = sys.stdin.isatty()
    if not yes and not dry_run and not interactive:
        typer.echo(
            "refused: install in a non-interactive environment requires --yes or --dry-run",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)
    if interactive and not yes and not dry_run:
        scope = typer.prompt("Scope (project|workspace|user)", default=scope)
        rp = typer.prompt("Runtime profile (local|aws|opentelemetry)", default=rp)
        if rp not in RUNTIME_PROFILES:
            raise _fail(f"unknown runtime profile '{rp}'")
        if scope not in SCOPES:
            raise _fail(f"unknown scope '{scope}'")

    tgt = _resolve_target(scope, root, workspace)
    ws = tgt.ws
    receipt = installation.base_receipt(
        operation="install",
        scope=scope,
        profile=tier,
        target_root=tgt.target_root,
        state_dir=ws.root,
        dry_run=dry_run,
    )
    receipt["components"] = {
        "install_tier": tier,
        "runtime_profile": rp,
        "gateway_profile": gp,
        "requested": list(comp) or None,
        "honored": [],
        "note": "service bundle — no optional install components; the tier "
        "selects nothing extra (all tiers install the same artifacts)",
    }
    if hosts:
        receipt["components"]["hosts_requested"] = list(hosts)
    if warnings:
        receipt["warnings"] = warnings
    if tgt.notes:
        receipt.setdefault("warnings", []).extend(tgt.notes)
    if scope == "workspace" and tgt.target_root is not None:
        members = installation.discover_members(tgt.target_root)
        receipt["workspace_members"] = {
            "discovery": "depth-1 markers (.git/pyproject.toml/package.json/forge.json)",
            "install": "deferred — members are observation targets, not install targets",
            "count": len(members),
            "members": members,
        }

    planned = _managed_plan(tgt)
    if dry_run:
        receipt["planned_files"] = planned
        receipt["status"] = "planned"
        receipt["verification"] = {"status": "UNVERIFIED"}
        _emit_receipt(receipt, as_json)
        return

    # -- apply -------------------------------------------------------------
    try:
        config_note, resolved_token = _apply_state(
            ws, scope=scope, runtime_profile=rp, tier=tier, gateway=gp
        )
    except Exception as exc:
        receipt["checks"].append(_check("workspace-state", "FAIL", str(exc)[:200]))
        receipt["status"] = "failed"
        receipt["verification"] = {"status": "FAIL"}
        _persist_and_emit(receipt, ws, as_json)
        raise typer.Exit(EXIT_ERROR) from exc
    receipt["runtime_profile"] = config_note
    receipt["managed_files"] = _managed_files(tgt, planned)

    checks: list[dict[str, Any]] = [
        _check("workspace-state", "PASS", f"state dir + config at {ws.root}"),
        _check("database", "PASS" if ws.db_path.exists() else "FAIL", str(ws.db_path)),
        _migrations_check(ws),
        _token_check(ws, resolved_token),
    ]
    context = _register_context(tgt, tier, rp, gp)
    checks.append(
        _check(
            "context-registry",
            {"registered": "PASS", "skipped": "NOT_APPLICABLE", "blocked": "BLOCKED"}[
                context["status"]
            ],
            context["detail"],
        )
    )
    account = _register_account(tgt)
    checks.append(account)
    if hosts:
        checks.append(
            _check(
                "host-assets",
                "NOT_APPLICABLE",
                f"service forge writes no host assets — recorded hosts: {','.join(hosts)}",
            )
        )
    if comp:
        checks.append(
            _check(
                "components",
                "NOT_APPLICABLE",
                f"no optional install components — unhonored: {','.join(comp)}",
            )
        )
    receipt["checks"] = checks
    receipt["verification"] = {"status": installation.verification_status(checks)}
    receipt["status"] = (
        "completed" if receipt["verification"]["status"] == "PASS" else "failed"
    )
    if context["status"] == "blocked" or account["status"] == "BLOCKED":
        receipt["status"] = "failed"
    _persist_and_emit(receipt, ws, as_json, token=resolved_token)
    if receipt["status"] != "completed":
        raise typer.Exit(EXIT_ERROR)


def _apply_state(
    ws: Any, *, scope: str, runtime_profile: str, tier: str, gateway: str
) -> tuple[dict[str, Any], Any]:
    """State dir + config + migrations + token — the service bundle.

    Returns (receipt-safe profile note, ResolvedToken). The token object
    NEVER goes inside the receipt — ``json.dumps(default=str)`` would
    serialize ``repr(resolved)`` including the secret (secrets hygiene:
    receipts carry the token's path, never the token)."""
    existed = ws.config_path.exists()
    previous = _load_config_dict(ws) if existed else {}
    ensure_workspace(ws, profile=runtime_profile)
    config_note = _sync_config(
        ws,
        previous=previous,
        existed=existed,
        scope=scope,
        runtime_profile=runtime_profile,
        install_tier=tier,
        gateway_profile=gateway,
    )
    with SqliteUnitOfWork(ws.db_path):  # applies migrations
        pass
    from api_sentinel.infrastructure.security.auth import ensure_local_token

    resolved = ensure_local_token(ws)
    return config_note, resolved


def _migrations_check(ws: Any) -> dict[str, Any]:
    from api_sentinel.infrastructure.storage.db import connect
    from api_sentinel.infrastructure.storage.migrations import applied_versions, discover

    try:
        conn = connect(ws.db_path)
        try:
            applied = applied_versions(conn)
        finally:
            conn.close()
        discovered = {v for v, _n, _p in discover()}
        missing = sorted(discovered - applied)
        if missing:
            return _check("migrations", "FAIL", f"pending versions: {missing}")
        return _check("migrations", "PASS", f"{len(applied)} applied")
    except Exception as exc:
        return _check("migrations", "FAIL", f"unreadable db: {exc}"[:200])


def _token_check(ws: Any, resolved: Any) -> dict[str, Any]:
    if resolved.path is None:
        return _check(
            "token",
            "NOT_APPLICABLE",
            "env-provided token in use — no workspace token file",
        )
    if resolved.path.exists():
        mode = "created" if resolved.created else ("rotated" if resolved.rotated else "reused")
        return _check("token", "PASS", f"token file {mode} at {resolved.path}")
    return _check("token", "FAIL", "token resolution produced no file")


def _managed_plan(tgt: InstallTarget) -> list[dict[str, str]]:
    """Deterministic artifact set the install manages — paths relative to
    the target root when under it, absolute otherwise."""
    ws = tgt.ws
    out = [
        {"path": str(ws.root), "kind": "state-dir", "action": "create"},
        {"path": str(ws.config_path), "kind": "config", "action": "create"},
        {"path": str(ws.db_path), "kind": "db", "action": "create"},
        {"path": str(ws.root / "token"), "kind": "token", "action": "create"},
        {"path": str(ws.root / "install" / "receipts"), "kind": "receipts", "action": "create"},
        {
            "path": str(installation.installations_dir() / f"{FORGE_ID}.json"),
            "kind": "account-manifest",
            "action": "create",
        },
    ]
    if tgt.context_root is not None:
        ctx = tgt.context_root / ".forge" / "context"
        out += [
            {
                "path": str(ctx / "capabilities" / f"{FORGE_ID}.json"),
                "kind": "context-manifest",
                "action": "create",
            },
            {"path": str(ctx / "registry.json"), "kind": "context-registry", "action": "update"},
            {"path": str(ctx / "ROUTING.md"), "kind": "context-routing", "action": "update"},
            {
                "path": str(tgt.context_root / "AGENTS.md"),
                "kind": "consumer-doc",
                "action": "marker-block",
            },
            {
                "path": str(tgt.context_root / "CLAUDE.md"),
                "kind": "consumer-doc",
                "action": "marker-block",
            },
            {
                "path": str(tgt.context_root / "AGENT_PROTOCOL.md"),
                "kind": "consumer-doc",
                "action": "marker-block",
            },
        ]
    return out


def _managed_files(tgt: InstallTarget, planned: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for entry in planned:
        p = Path(entry["path"])
        action = "update" if p.exists() and entry["action"] != "marker-block" else entry["action"]
        out.append({**entry, "action": action})
    return out


def _register_account(tgt: InstallTarget) -> dict[str, Any]:
    try:
        path = installation.register_installation(tgt.target_root, tgt.ws)
    except OSError as exc:
        return _check("account-registry", "BLOCKED", f"cannot write manifest: {exc}"[:200])
    return _check("account-registry", "PASS", f"manifest at {path}")


def _persist_and_emit(
    receipt: dict[str, Any], ws: Any, as_json: bool, *, token: Any = None
) -> None:
    path = installation.write_receipt(ws, receipt)
    receipt["receipt_path"] = str(path)
    _emit_receipt(receipt, as_json, token=token)


def _emit_receipt(receipt: dict[str, Any], as_json: bool, *, token: Any = None) -> None:
    if as_json:
        typer.echo(json.dumps(receipt, default=str))
        return
    typer.echo(f"{receipt['operation']}: {receipt['status']}")
    if receipt.get("target_root"):
        typer.echo(f"  target root: {receipt['target_root']}")
    typer.echo(f"  state dir:   {receipt['state_dir']}")
    comp = receipt.get("components") or {}
    typer.echo(
        f"  profile: {receipt['profile']} (tier) · runtime {comp.get('runtime_profile')} "
        f"· gateway {comp.get('gateway_profile')}"
    )
    for f in receipt.get("planned_files", receipt.get("managed_files", [])):
        typer.echo(f"  {f.get('action', '-'):13} {f['path']}")
    for c in receipt.get("checks", []):
        typer.echo(f"  [{c.get('status', '?')}] {c.get('id', '?')} {c.get('detail', '')}")
    for w in receipt.get("warnings", []):
        typer.echo(f"  warning: {w}")
    if receipt.get("receipt_path"):
        typer.echo(f"  receipt: {receipt['receipt_path']}")
    if token is not None and getattr(token, "created", False):
        # intentional one-time print — the token never enters receipts/JSON
        typer.echo(
            f"  API token generated: {token.path}\n"
            f"    Authorization: Bearer {token.token}\n"
            "    shown once — read the token file to retrieve it later"
        )
    if _portal_present():
        typer.echo("  Forge Portal detected")
    else:
        typer.echo("  Forge Portal not found — API Operations activates when available")


def _portal_present() -> bool:
    return shutil.which("forge-portal") is not None


# --------------------------------------------------------------------------
# update / uninstall (top-level commands) + install sub-app ops
# --------------------------------------------------------------------------


def update_command(
    workspace: _WS = None,
    scope: _SCOPE = "project",
    root: _ROOT = None,
    as_json: _JSON = False,
    dry_run: _DRY = False,
) -> None:
    """Re-apply migrations and refresh the local token (idempotent)."""
    tgt = _resolve_target(scope, root, workspace)
    ws = tgt.ws
    if not ws.config_path.exists():
        # the installed marker — an existing-but-uninitialized dir is NOT
        # an install: updating it would silently create a db (the old
        # buggy guard let exactly that through).
        typer.echo(
            json.dumps({"updated": False, "reason": "workspace_not_installed"})
            if as_json
            else f"Workspace not installed at {ws.root} — run `api-sentinel install`",
            err=not as_json,
        )
        raise typer.Exit(EXIT_USAGE)
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
    context = _converge_context(tgt)
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


def _assert_sentinel_owned(ws: Any) -> None:
    """Ownership proof before any destructive op: the dir must carry at
    least one sentinel-shaped marker (config/db/token/known subdirs) and
    must never be the filesystem root or the user's home."""
    root = ws.root
    if root.parent == root or root == Path.home():
        typer.echo(
            f"refused: {root} is not a sentinel workspace "
            "(filesystem/home root) — refusing removal",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)
    markers = (
        "config.yaml",
        "sentinel.db",
        "token",
        "blobs",
        "handoffs",
        "receipts",
        "agent_sessions",
        "install",
    )
    if root.exists() and not any((root / m).exists() for m in markers):
        typer.echo(
            f"refused: {root} carries no sentinel markers — not a sentinel "
            "workspace; refusing to remove arbitrary data",
            err=True,
        )
        raise typer.Exit(EXIT_USAGE)


def _do_uninstall(
    tgt: InstallTarget,
    *,
    purge: bool,
    yes: bool,
    dry_run: bool,
    as_json: bool,
) -> None:
    ws = tgt.ws
    residue = tgt.context_root is not None and _docs_leftovers(tgt.context_root)
    account_present = bool(installation.installation_manifest())
    receipt = installation.base_receipt(
        operation="uninstall",
        scope=ws.scope,
        profile="recommended",
        target_root=tgt.target_root,
        state_dir=ws.root,
        dry_run=dry_run,
    )
    receipt["purge"] = purge
    if not ws.root.exists() and not residue and not account_present:
        receipt["status"] = "completed"
        receipt["checks"] = [
            _check("state", "NOT_APPLICABLE", "nothing installed — nothing to remove")
        ]
        receipt["verification"] = {"status": "PASS"}
        _emit_receipt(receipt, as_json)
        return
    _assert_sentinel_owned(ws)
    if dry_run:
        receipt["status"] = "planned"
        receipt["would_remove"] = [
            str(tgt.context_root / ".forge" / "context") if residue else None,
            str(installation.installations_dir() / f"{FORGE_ID}.json")
            if account_present
            else None,
            str(ws.root) if purge else None,
        ]
        receipt["would_remove"] = [p for p in receipt["would_remove"] if p]
        receipt["would_keep"] = [] if purge else [str(ws.root)]
        receipt["verification"] = {"status": "UNVERIFIED"}
        _emit_receipt(receipt, as_json)
        return
    _require_tty_or_yes("uninstall", yes=yes, dry_run=False)

    removed: list[str] = []
    kept: list[str] = []
    checks: list[dict[str, Any]] = []
    context = _unregister_context(tgt) if residue else {
        "status": "skipped",
        "detail": "no context residue",
    }
    checks.append(
        _check(
            "context-registry",
            {"unregistered": "PASS", "skipped": "NOT_APPLICABLE", "blocked": "BLOCKED"}[
                context["status"]
            ],
            context["detail"],
        )
    )
    if context["status"] == "unregistered":
        removed.append("context-registration")
    if installation.deregister_installation():
        removed.append(str(installation.installations_dir() / f"{FORGE_ID}.json"))
        checks.append(_check("account-registry", "PASS", "manifest removed"))
    else:
        checks.append(
            _check("account-registry", "NOT_APPLICABLE", "no manifest present")
        )
    if purge:
        try:
            shutil.rmtree(ws.root)
            removed.append(str(ws.root))
            checks.append(_check("state-purge", "PASS", f"removed {ws.root}"))
        except OSError as exc:
            kept.append(str(ws.root))
            checks.append(
                _check(
                    "state-purge",
                    "FAIL",
                    f"cannot remove {ws.root}: {exc} "
                    "(close processes holding it; on Windows do not run from inside it)"[:200],
                )
            )
    else:
        kept.append(str(ws.root))
        checks.append(
            _check(
                "state-purge",
                "NOT_APPLICABLE",
                "keep-data default — state dir preserved; pass --purge to remove it",
            )
        )
    receipt["removed"] = removed
    receipt["kept"] = kept
    receipt["checks"] = checks
    receipt["verification"] = {"status": installation.verification_status(checks)}
    receipt["status"] = (
        "completed" if receipt["verification"]["status"] == "PASS" else "failed"
    )
    if not purge:
        # keep-data: the receipt itself is part of the preserved state.
        _persist_and_emit(receipt, ws, as_json)
    else:
        _emit_receipt(receipt, as_json)
    if receipt["status"] != "completed":
        raise typer.Exit(EXIT_ERROR)


def uninstall_command(
    workspace: _WS = None,
    scope: _SCOPE = "project",
    root: _ROOT = None,
    yes: _YES = False,
    as_json: _JSON = False,
    dry_run: _DRY = False,
    purge: Annotated[
        bool, typer.Option("--purge", help="Also remove the state dir (data loss)")
    ] = False,
) -> None:
    """Detach the install: context + account registrations out, state kept
    unless ``--purge``. Never silent — requires --yes or a TTY confirm."""
    tgt = _resolve_target(scope, root, workspace)
    _do_uninstall(tgt, purge=purge, yes=yes, dry_run=dry_run, as_json=as_json)


# --------------------------------------------------------------------------
# install sub-app ops — status / doctor / repair / uninstall / mcp-verify
# --------------------------------------------------------------------------


def _health_doc(
    tgt: InstallTarget, checks: list[dict[str, Any]], *, extra: dict[str, Any]
) -> dict[str, Any]:
    state = installation.probe_state(tgt.ws, context_root=tgt.context_root)
    installed = state.config_present
    # "healthy" only when every check is PASS or NOT_APPLICABLE — a
    # FAIL/BLOCKED/UNVERIFIED check degrades the verdict, never
    # disappears into success.
    if not all(c["status"] in ("PASS", "NOT_APPLICABLE") for c in checks):
        status = "degraded" if installed else "broken"
    elif not installed:
        status = "unverified"
    else:
        status = "healthy"
    return {
        "schema": installation.SCHEMA_HEALTH,
        "forge_id": FORGE_ID,
        "status": status,
        "installed": installed,
        "scope": tgt.ws.scope,
        "state_dir": str(tgt.ws.root),
        "target_root": str(tgt.target_root) if tgt.target_root else None,
        "checks": checks,
        "receipts": installation.list_receipts(tgt.ws),
        "checked_at": installation._utc_now(),
        "repair_hint": "api-sentinel install repair" if status != "healthy" else None,
        **extra,
    }


def _probe_checks(tgt: InstallTarget) -> list[dict[str, Any]]:
    state = installation.probe_state(tgt.ws, context_root=tgt.context_root)
    checks: list[dict[str, Any]] = []
    if not state.state_dir_present:
        return [
            _check(
                "workspace-state",
                "FAIL",
                f"no state dir at {tgt.ws.root} — workspace not installed",
            )
        ]
    checks.append(
        _check(
            "workspace-state",
            "PASS" if state.config_present else "FAIL",
            "config.yaml present" if state.config_present else "config.yaml missing",
        )
    )
    checks.append(
        _check("database", "PASS" if state.db_present else "FAIL", str(tgt.ws.db_path))
    )
    if state.migrations_applied is None:
        mig = _check("migrations", "UNVERIFIED", "db unreadable")
    elif state.migrations_applied < state.migrations_discovered:
        mig = _check(
            "migrations",
            "FAIL",
            f"{state.migrations_applied}/{state.migrations_discovered} applied — pending",
        )
    else:
        mig = _check(
            "migrations",
            "PASS",
            f"{state.migrations_applied}/{state.migrations_discovered} applied",
        )
    checks.append(mig)
    checks.append(
        _check(
            "token",
            "PASS" if state.token_present else "NOT_APPLICABLE",
            "workspace token file present"
            if state.token_present
            else "no token file (env token may still serve)",
        )
    )
    if state.context_registered is None:
        checks.append(
            _check("context-registry", "NOT_APPLICABLE", "no context layer for this scope")
        )
    else:
        checks.append(
            _check(
                "context-registry",
                "PASS" if state.context_registered else "FAIL",
                "registered in .forge/context"
                if state.context_registered
                else "not registered in .forge/context",
            )
        )
    checks.append(
        _check(
            "account-registry",
            "PASS" if state.account_registered else "UNVERIFIED",
            "manifest in ~/.forge/installations"
            if state.account_registered
            else "no ~/.forge/installations manifest",
        )
    )
    return checks


@install_app.command("status")
def install_status(
    scope: _SCOPE = "project",
    root: _ROOT = None,
    workspace: _WS = None,
    as_json: _JSON = False,
) -> None:
    """Install state — workspace, registrations and receipts (read-only)."""
    tgt = _resolve_target(scope, root, workspace)
    checks = _probe_checks(tgt)
    extra: dict[str, Any] = {}
    if tgt.ws.scope == "workspace" and tgt.target_root is not None:
        extra["workspace_members"] = installation.discover_members(tgt.target_root)
    doc = _health_doc(tgt, checks, extra=extra)
    _emit_health(doc, as_json)


@install_app.command("doctor")
def install_doctor(
    scope: _SCOPE = "project",
    root: _ROOT = None,
    workspace: _WS = None,
    as_json: _JSON = False,
) -> None:
    """Environment + install health — probe checks plus python and MCP."""
    tgt = _resolve_target(scope, root, workspace)
    checks = _probe_checks(tgt)
    checks.append(
        _check(
            "python",
            "PASS" if sys.version_info >= (3, 11) else "FAIL",
            f"python {sys.version.split()[0]}",
        )
    )
    checks.append(installation.mcp_verify(tgt.ws))
    doc = _health_doc(tgt, checks, extra={})
    _emit_health(doc, as_json)


def _emit_health(doc: dict[str, Any], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(doc, default=str))
    else:
        typer.echo(f"{doc['status']} — {doc['state_dir']}")
        for c in doc.get("checks", []):
            typer.echo(f"  [{c.get('status', '?')}] {c.get('id', '?')} {c.get('detail', '')}")
        if doc.get("repair_hint"):
            typer.echo(f"  repair: {doc['repair_hint']}")
    # Canonical pass-set: named non-failed states exit 0; degraded/broken
    # never read as silent success.
    if doc.get("status") not in ("completed", "planned", "healthy", "unverified", "PASS"):
        raise typer.Exit(EXIT_ERROR)


@install_app.command("repair")
def install_repair(
    scope: _SCOPE = "project",
    root: _ROOT = None,
    workspace: _WS = None,
    yes: _YES = False,
    dry_run: _DRY = False,
    as_json: _JSON = False,
) -> None:
    """Converge a degraded install: migrations, token reuse, context repair."""
    tgt = _resolve_target(scope, root, workspace)
    ws = tgt.ws
    residue = tgt.context_root is not None and (
        tgt.context_root / ".forge" / "context"
    ).exists()
    if not ws.root.exists() and not residue:
        typer.echo(
            json.dumps(
                {"schema": installation.SCHEMA_RECEIPT, "operation": "repair",
                 "status": "failed", "reason": "workspace_absent"}
            )
            if as_json
            else f"Nothing to repair at {ws.root} — run `api-sentinel install`",
            err=not as_json,
        )
        raise typer.Exit(EXIT_USAGE)
    if dry_run:
        doc = _health_doc(tgt, _probe_checks(tgt), extra={})
        doc["operation"] = "repair"
        doc["status"] = "planned"
        _emit_health(doc, as_json)
        return
    _require_tty_or_yes("repair", yes=yes, dry_run=False)

    receipt = installation.base_receipt(
        operation="repair",
        scope=ws.scope,
        profile="recommended",
        target_root=tgt.target_root,
        state_dir=ws.root,
        dry_run=False,
    )
    repaired: list[str] = []
    kept: list[str] = []
    skipped: list[str] = []
    checks: list[dict[str, Any]] = []
    try:
        ensure_workspace(ws)
        with SqliteUnitOfWork(ws.db_path):
            pass
        checks.append(_check("migrations", "PASS", "migrations re-applied (idempotent)"))
        repaired.append("migrations")
    except Exception as exc:
        checks.append(_check("migrations", "FAIL", str(exc)[:200]))
        skipped.append("migrations")
    from api_sentinel.infrastructure.security.auth import ensure_local_token

    resolved = ensure_local_token(ws)
    checks.append(_token_check(ws, resolved))
    kept.append("token" if not resolved.created else "token(new)")
    context = _converge_context(tgt)
    if context is None:
        checks.append(
            _check("context-registry", "NOT_APPLICABLE", "no context layer present")
        )
    else:
        status = {"repaired": "PASS", "registered": "PASS", "blocked": "BLOCKED"}.get(
            context["status"], "FAIL"
        )
        checks.append(_check("context-registry", status, context["detail"]))
        (repaired if status == "PASS" else skipped).append("context-registry")
    account = _register_account(tgt)
    checks.append(account)
    (repaired if account["status"] == "PASS" else skipped).append("account-registry")
    receipt["repaired"] = repaired
    receipt["kept"] = kept
    receipt["skipped"] = skipped
    receipt["checks"] = checks
    receipt["verification"] = {"status": installation.verification_status(checks)}
    receipt["status"] = (
        "completed" if receipt["verification"]["status"] == "PASS" else "failed"
    )
    _persist_and_emit(receipt, ws, as_json)
    if receipt["status"] != "completed":
        raise typer.Exit(EXIT_ERROR)


@install_app.command("uninstall")
def install_uninstall(
    scope: _SCOPE = "project",
    root: _ROOT = None,
    workspace: _WS = None,
    yes: _YES = False,
    dry_run: _DRY = False,
    as_json: _JSON = False,
    purge: Annotated[
        bool, typer.Option("--purge", help="Also remove the state dir (data loss)")
    ] = False,
) -> None:
    """Detach the install; ``--purge`` removes the state dir."""
    tgt = _resolve_target(scope, root, workspace)
    _do_uninstall(tgt, purge=purge, yes=yes, dry_run=dry_run, as_json=as_json)


@install_app.command("mcp-verify")
def install_mcp_verify(
    scope: _SCOPE = "project",
    root: _ROOT = None,
    workspace: _WS = None,
    as_json: _JSON = False,
) -> None:
    """Verify the MCP handshake (initialize + tools/list + sentinel_health)."""
    tgt = _resolve_target(scope, root, workspace)
    check = installation.mcp_verify(tgt.ws)
    doc = {
        "schema": installation.SCHEMA_HEALTH,
        "forge_id": FORGE_ID,
        "status": (
            "healthy"
            if check["status"] == "PASS"
            else "degraded"
            if check["status"] == "FAIL"
            else "unverified"
        ),
        "checks": [check],
        "checked_at": installation._utc_now(),
        "repair_hint": None,
    }
    if as_json:
        typer.echo(json.dumps(doc, default=str))
    else:
        typer.echo(f"{doc['status']} — {check['detail']}")
    # Canonical: only a verified handshake exits 0 — FAIL, BLOCKED and
    # NOT_APPLICABLE/UNVERIFIED are not success.
    if doc.get("status") != "healthy":
        raise typer.Exit(EXIT_ERROR)


# --------------------------------------------------------------------------
# forge-context/v1 wiring (install-2.0 — same registry the api-forge/
# spark-aws/platform/data-sentinel/doctor siblings write). `.forge/context/`
# is the workspace-level registry shared between forges — a different layer
# from the sentinel workspace state (`.api-sentinel/`). This forge only ever
# registers/unregisters its own `forge_id`.
#
# Anchor (install-2.1): the context root comes from `resolve_install_target`
# — the explicit `--root` target root, or the parent of a
# ``<root>/.api-sentinel``-shaped state dir. A verbatim ``--workspace``
# that is not sentinel-shaped never derives a context root (the old code
# used `ws.root.parent` unconditionally — `--workspace /tmp/x` wrote
# `/tmp/AGENTS.md` + `/tmp/.forge/context`). `user` scope never registers.
# --------------------------------------------------------------------------

_CONSUMER_DOCS: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md", "AGENT_PROTOCOL.md")

_SENTINEL_DOMAINS: tuple[str, ...] = (
    "api-reliability",
    "runtime-observation",
    "slo-assessment",
    "anomaly-detection",
    "incident-investigation",
    "deployment-correlation",
)


def _bundled_json(name: str) -> dict[str, Any] | None:
    return installation.bundled_json(name)


def _context_manifest(
    tgt: InstallTarget,
    tier: str,
    runtime_profile: str,
    gateway_profile: str,
) -> dict[str, Any]:
    """The ``forge/context/v1`` manifest built from the real sources:
    ``forge.json`` / ``forge.agentic.json`` and the declared capability
    surface."""
    from api_sentinel import _forgecontext as fctx

    return fctx.build_manifest(
        forge_id=FORGE_ID,
        name="API Sentinel Forge",
        version=__version__,
        cli="api-sentinel",
        package="api_sentinel",
        distribution="api-sentinel-forge",
        python_spec=">=3.11",
        scope=tgt.ws.scope,
        # `installed.profile` is the installkit disclosure tier; the
        # runtime profile (local|aws|opentelemetry) and the advisory
        # gateway profile are recorded under `components`.
        profile=tier,
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
            "verify_tool": "sentinel_health",
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
        components={
            "runtime_profile": runtime_profile,
            "gateway_profile": gateway_profile,
            "installation_kind": "service",
        },
        recorded_by="api-sentinel",
    )


def _register_context(
    tgt: InstallTarget,
    tier: str,
    runtime_profile: str,
    gateway_profile: str,
) -> dict[str, Any]:
    """Write `.forge/context/` — manifest + registry entry + ROUTING.md +
    consumer docs — in ONE transaction. Returns the honest outcome for the
    payload: registered / skipped (no honest context root) / blocked
    (named refusal)."""
    from api_sentinel import _forgecontext as fctx

    root = tgt.context_root
    if root is None:
        detail = (
            "account scope — no workspace context"
            if tgt.ws.scope == "user"
            else "no context root derivable — pass --root for registration"
        )
        return {"status": "skipped", "detail": detail}
    try:
        out = fctx.install_transaction(
            root, _context_manifest(tgt, tier, runtime_profile, gateway_profile), owner=FORGE_ID
        )
        docs = out["docs"]
        return {
            "status": "registered",
            "detail": f".forge/context {out['status']} "
            f"({len(out['written'])} writes; docs {docs['status']})",
            "root": str(root),
        }
    except fctx.ContextError as exc:
        return {"status": "blocked", "detail": f"{exc.kind}: {exc.detail}"}


def _unregister_context(tgt: InstallTarget) -> dict[str, Any]:
    """Remove this forge's manifest/entry/`forge:*` blocks in ONE
    transaction — only when residue exists (the transaction acquires its
    own lock and would create `.forge/context/` on a pristine root)."""
    from api_sentinel import _forgecontext as fctx

    root = tgt.context_root
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


def _repair_context(tgt: InstallTarget) -> dict[str, Any] | None:
    """Converge an existing `.forge/context/` — journal recovery, stale
    locks, registry+ROUTING.md rebuild and `forge:*` doc re-assert. None
    when the layer was never installed (no residue created)."""
    from api_sentinel import _forgecontext as fctx

    root = tgt.context_root
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


def _converge_context(tgt: InstallTarget) -> dict[str, Any] | None:
    """Repair that also re-registers: `repair_context` rebuilds the
    registry from the manifests on disk, so a deleted capability
    manifest would drop this forge silently — converge re-runs the
    install transaction (recorded config as the intent), never just
    the structural repair."""
    from api_sentinel import _forgecontext as fctx

    root = tgt.context_root
    if root is None or not (root / fctx.CONTEXT_DIR).exists():
        return None
    cap = root / fctx.CONTEXT_DIR / "capabilities" / f"{FORGE_ID}.json"
    if not cap.exists():
        cfg = _load_config_dict(tgt.ws)
        return _register_context(
            tgt,
            str(cfg.get("install_profile") or "recommended"),
            str(cfg.get("profile") or "local"),
            str(cfg.get("gateway_profile") or "balanced"),
        )
    return _repair_context(tgt)


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
