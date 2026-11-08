"""api-sentinel CLI — Typer app with --json output and stable exit codes."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from api_sentinel import __version__
from api_sentinel.application.use_cases import (
    DefineSlo,
    EvaluateSlo,
    GetServiceHealth,
    ListServices,
    ListSloDefinitions,
    RegisterService,
    RegisterSource,
)
from api_sentinel.capabilities import list_capabilities, status_report
from api_sentinel.cli.install import install_command
from api_sentinel.domain import (
    NotFoundError,
    SentinelError,
    SystemClock,
    UlidIdFactory,
    ValidationError,
)
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.metrics import ErrorClassification, parse_duration_seconds
from api_sentinel.reliability import SliKind
from api_sentinel.workspace import Workspace, ensure_workspace, resolve_workspace

app = typer.Typer(
    name="api-sentinel",
    help="API Sentinel Forge — observe, correlate, diagnose, verify.",
    no_args_is_help=True,
    add_completion=False,
)
slo_app = typer.Typer(
    name="slo",
    help="SLO definitions and windowed assessments.",
    no_args_is_help=True,
)
app.add_typer(slo_app, name="slo")

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


@app.command()
def ingest(
    source_id: Annotated[
        str, typer.Option("--source-id", help="Registered telemetry source id")
    ],
    file: Annotated[
        Path, typer.Option("--file", help="Batch payload path (JSONL or OTLP JSON)")
    ],
    kind: Annotated[
        str | None, typer.Option("--kind", help="jsonl | otlp-json (auto-detected if omitted)")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Ingest a telemetry batch end-to-end; idempotent re-runs never duplicate."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    with uow:
        source = uow.sources.get(source_id)
        if source is None:
            raise NotFoundError(f"telemetry source not found: {source_id}")
        if not source.enabled:
            raise ValidationError(f"telemetry source is disabled: {source_id}")
    try:
        content = file.read_bytes()
    except OSError as exc:
        raise ValidationError(f"cannot read batch file: {file}") from exc
    result = IngestionPipeline(
        uow, SystemClock(), UlidIdFactory(), lambda t: redact(t).text
    ).ingest(source=source, kind=kind, content=content)
    _emit(
        {
            "batch_id": result.batch_id,
            "accepted": result.accepted,
            "quarantined": result.quarantined,
            "skipped_duplicates": result.skipped_duplicates,
        },
        as_json,
    )


app.command("install")(install_command)


def _parse_error_status(raw: str | None) -> ErrorClassification | None:
    """'5xx,503,429' → ErrorClassification (classes stay classes, codes codes)."""
    if raw is None:
        return None
    classes: list[str] = []
    codes: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if len(token) == 3 and token[0].isdigit() and token[1:] == "xx":
            classes.append(token)
        else:
            try:
                codes.append(int(token))
            except ValueError as exc:
                raise ValidationError(
                    f"invalid --error-status token: {token!r}",
                    details={"expected": "CSV of status codes or classes, e.g. '5xx,429'"},
                ) from exc
    try:
        return ErrorClassification(
            error_status_classes=classes or ["5xx"], error_status_codes=codes
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _parse_dt(raw: str, flag: str) -> datetime:
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(
            f"invalid {flag} datetime: {raw!r} (expected ISO-8601)"
        ) from exc


@slo_app.command("define")
def slo_define(
    service_id: Annotated[str, typer.Option("--service-id")],
    target: Annotated[float, typer.Option("--target", help="Objective S, e.g. 0.99")],
    window: Annotated[str, typer.Option("--window", help="e.g. 60s, 15m, 1h, 24h")],
    revision: Annotated[int | None, typer.Option("--revision")] = None,
    slo_id: Annotated[str | None, typer.Option("--slo-id")] = None,
    sli: Annotated[str, typer.Option("--sli", help="availability | latency")] = "availability",
    latency_threshold_ms: Annotated[
        float | None, typer.Option("--latency-threshold-ms")
    ] = None,
    operation_id: Annotated[str | None, typer.Option("--operation-id")] = None,
    error_status: Annotated[
        str | None,
        typer.Option("--error-status", help="CSV codes/classes, e.g. '5xx,429'"),
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Define a versioned SLO; same revision+same definition is idempotent."""
    try:
        window_seconds = parse_duration_seconds(window)
        sli_kind = SliKind(sli)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    definition = DefineSlo(uow, SystemClock(), UlidIdFactory()).execute(
        service_id=service_id,
        target=target,
        window_seconds=window_seconds,
        revision=revision,
        sli_kind=sli_kind,
        operation_id=operation_id,
        latency_ms=latency_threshold_ms,
        error_classification=_parse_error_status(error_status),
        slo_id=slo_id,
    )
    _emit(definition.model_dump(mode="json"), as_json)


@slo_app.command("evaluate")
def slo_evaluate(
    slo_id: Annotated[str, typer.Option("--slo-id")],
    window_start: Annotated[str, typer.Option("--window-start", help="ISO-8601")],
    window_end: Annotated[str, typer.Option("--window-end", help="ISO-8601")],
    revision: Annotated[int | None, typer.Option("--revision")] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Evaluate an SLO over a window; persisted idempotently per slot."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    assessment = EvaluateSlo(uow, SystemClock(), UlidIdFactory()).execute(
        slo_id=slo_id,
        revision=revision,
        window_start=_parse_dt(window_start, "--window-start"),
        window_end=_parse_dt(window_end, "--window-end"),
    )
    _emit(assessment.model_dump(mode="json"), as_json)


@slo_app.command("list")
def slo_list(
    service_id: Annotated[str | None, typer.Option("--service-id")] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """List SLO definitions (all revisions)."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    defs = ListSloDefinitions(uow).execute(service_id=service_id)
    _emit([d.model_dump(mode="json") for d in defs], as_json)


@app.command()
def health(
    service_id: Annotated[str, typer.Option("--service-id")],
    stale_after_seconds: Annotated[
        int, typer.Option("--stale-after-seconds")
    ] = 300,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Per-service health: state, latest indicators, unknowns (LAB-007)."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    report = GetServiceHealth(uow, SystemClock()).execute(
        service_id=service_id, stale_after_seconds=stale_after_seconds
    )
    _emit(report.model_dump(mode="json"), as_json)


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
