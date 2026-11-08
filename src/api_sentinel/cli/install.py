"""Guided install — profile + scope selection; headless via --yes."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated, cast

import typer

from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.workspace import Scope, ensure_workspace, resolve_workspace

PROFILES = ("local", "aws", "opentelemetry")
SCOPES = ("project", "user")


def install_command(
    profile: Annotated[
        str, typer.Option("--profile", help="local | aws | opentelemetry")
    ] = "local",
    scope: Annotated[str, typer.Option("--scope", help="project | user")] = "project",
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Non-interactive")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
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
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "workspace": str(ws.root),
                    "profile": profile,
                    "scope": scope,
                    "portal": _portal_present(),
                }
            )
        )
        return
    typer.echo(f"Workspace ready: {ws.root}")
    typer.echo(f"Profile: {profile}  Scope: {scope}")
    if _portal_present():
        typer.echo("Forge Portal detected")
    else:
        typer.echo("Forge Portal not found — API Operations activates when available")


def _portal_present() -> bool:
    return shutil.which("forge-portal") is not None
