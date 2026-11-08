"""api-sentinel CLI — Typer app with --json output and stable exit codes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from api_sentinel import __version__
from api_sentinel.application.use_cases import ListServices, RegisterService, RegisterSource
from api_sentinel.capabilities import list_capabilities, status_report
from api_sentinel.cli.install import install_command
from api_sentinel.domain import SentinelError, SystemClock, UlidIdFactory
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.workspace import Workspace, ensure_workspace, resolve_workspace

app = typer.Typer(
    name="api-sentinel",
    help="API Sentinel Forge — observe, correlate, diagnose, verify.",
    no_args_is_help=True,
    add_completion=False,
)

_JSON = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")]
_WS = Annotated[str | None, typer.Option("--workspace", help="Explicit workspace root")]
_USER_SCOPE = Annotated[bool, typer.Option("--user", help="Use the user-level workspace")]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(payload, default=str))
    else:
        if isinstance(payload, list):
            for item in payload:
                typer.echo(_human(item))
        else:
            typer.echo(_human(payload))


def _human(item: Any) -> str:
    if isinstance(item, dict):
        return "  ".join(f"{k}={v}" for k, v in item.items())
    return str(item)


def _ws(workspace: str | None, user_scope: bool) -> Workspace:
    return resolve_workspace(
        scope="user" if user_scope else "project",
        explicit=Path(workspace) if workspace else None,
    )


def _uow(ws: Workspace) -> SqliteUnitOfWork:
    ensure_workspace(ws)
    return SqliteUnitOfWork(ws.db_path)


@app.callback()
def _main() -> None:
    pass


@app.command()
def version(as_json: _JSON = False) -> None:
    """Print the package version."""
    _emit({"version": __version__}, as_json)


@app.command()
def doctor(
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Self-check: workspace, database, capabilities."""
    ws = _ws(workspace, user_scope)
    report = status_report(
        workspace_initialized=ws.root.exists(),
        db_present=ws.db_path.exists(),
    )
    report["workspace_root"] = str(ws.root)
    report["version"] = __version__
    _emit(report, as_json)


@app.command()
def status(
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Report current workspace state."""
    doctor(as_json=as_json, workspace=workspace, user_scope=user_scope)


@app.command()
def capabilities(as_json: _JSON = False) -> None:
    """List declared capabilities and their real status."""
    caps = [
        {
            "capability_id": c.capability_id,
            "status": c.status.value,
            "since": c.since,
            "notes": c.notes,
        }
        for c in list_capabilities()
    ]
    _emit(caps, as_json)


@app.command("services")
def services_cmd(
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
    register: Annotated[
        str | None, typer.Option("--register", help="Service name to register")
    ] = None,
    environment: Annotated[str, typer.Option("--environment")] = "local",
    namespace: Annotated[str | None, typer.Option("--namespace")] = None,
) -> None:
    """List services, or register one idempotently with --register."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    clock, ids = SystemClock(), UlidIdFactory()
    if register is not None:
        svc = RegisterService(uow, clock, ids).execute(
            service_name=register, environment=environment, namespace=namespace
        )
        _emit(
            {"service_id": svc.service_id, "service_name": svc.service_name,
             "environment_id": svc.environment_id, "created": True},
            as_json,
        )
        return
    out = [
        {"service_id": s.service_id, "service_name": s.service_name,
         "environment_id": s.environment_id, "namespace": s.namespace}
        for s in ListServices(uow).execute()
    ]
    _emit(out, as_json)


@app.command("sources")
def sources_cmd(
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
    register: Annotated[
        str | None, typer.Option("--register", help="Source kind to register")
    ] = None,
    service_id: Annotated[str | None, typer.Option("--service-id")] = None,
) -> None:
    """List telemetry sources, or register one with --register KIND --service-id ID."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    if register is not None:
        if not service_id:
            typer.echo("error: --service-id is required with --register", err=True)
            raise typer.Exit(EXIT_USAGE)
        src = RegisterSource(uow, SystemClock(), UlidIdFactory()).execute(
            kind=register, service_id=service_id
        )
        _emit({"source_id": src.source_id, "kind": src.kind, "service_id": src.service_id}, as_json)
        return
    with uow:
        out = [
            {"source_id": s.source_id, "kind": s.kind, "service_id": s.service_id,
             "enabled": s.enabled}
            for s in uow.sources.list()
        ]
    _emit(out, as_json)


@app.command()
def workspace(
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Show the resolved workspace root and contents."""
    ws = _ws(workspace, user_scope)
    _emit(
        {
            "root": str(ws.root),
            "scope": ws.scope,
            "initialized": ws.root.exists(),
            "db": str(ws.db_path),
            "db_present": ws.db_path.exists(),
        },
        as_json,
    )


app.command("install")(install_command)


def _dispatch() -> int:
    """Entry point with domain-error exit-code mapping."""
    try:
        app()
    except SentinelError as exc:
        typer.echo(f"error[{exc.code}]: {exc.message}", err=True)
        return EXIT_ERROR
    except typer.Exit as exc:
        return exc.exit_code or 0
    return EXIT_OK


def main() -> None:
    sys.exit(_dispatch())
