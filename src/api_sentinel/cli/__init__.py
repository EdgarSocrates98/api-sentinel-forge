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
    ListIncidents,
    ListServices,
    ListSloDefinitions,
    RegisterService,
    RegisterSource,
    RunDetection,
    ShowIncident,
    TransitionIncident,
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
from api_sentinel.incidents import IncidentStatus
from api_sentinel.infrastructure.security import redact
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.ingestion import IngestionPipeline
from api_sentinel.integrations.aws.client import (
    AwsClientSet,
    AwsSessionConfig,
)
from api_sentinel.integrations.service import (
    Correlate,
    DelegateToDoctor,
    ListChanges,
    ListDependencies,
    RecordChange,
    RecordDependency,
    doctor_acl_from_config,
    load_structural,
)
from api_sentinel.integrations.status import integration_status
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
incident_app = typer.Typer(
    name="incident",
    help="Incident lifecycle: show/ack/resolve/reopen (journal-appended).",
    no_args_is_help=True,
)
app.add_typer(incident_app, name="incident")
handoff_app = typer.Typer(
    name="handoff",
    help="Cross-Forge handoffs: delegate, ingest, audit.",
    no_args_is_help=True,
)
app.add_typer(handoff_app, name="handoff")
changes_app = typer.Typer(
    name="changes",
    help="Change intelligence: list/record/import/correlate (Cycle 8).",
    no_args_is_help=True,
)
app.add_typer(changes_app, name="changes")
hypotheses_app = typer.Typer(
    name="hypotheses",
    help="Hypothesis lifecycle: add/promote/demote/verify (Cycle 8).",
    no_args_is_help=True,
)
app.add_typer(hypotheses_app, name="hypotheses")
aws_app = typer.Typer(
    name="aws",
    help="AWS read-only observability: collect/discover (ADR-013).",
    no_args_is_help=True,
)
app.add_typer(aws_app, name="aws")
backup_app = typer.Typer(
    name="backup",
    help="Workspace backup/restore: verifiable snapshots (Cycle 9).",
    no_args_is_help=True,
)
app.add_typer(backup_app, name="backup")

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


def _load_cfg(ws: Workspace) -> dict[str, object]:
    from api_sentinel.workspace import load_config

    try:
        return load_config(ws)
    except Exception:  # workspace without config → no integrations
        return {}


def _atlas_path(config: dict[str, object], ws: Workspace) -> Path | None:
    integrations = config.get("integrations")
    if not isinstance(integrations, dict):
        return None
    atlas = integrations.get("atlas")
    if not isinstance(atlas, dict) or not atlas.get("export"):
        return None
    path = Path(str(atlas["export"]))
    return path if path.is_absolute() else ws.root / path


def _atlas_db_path(config: dict[str, object], ws: Workspace) -> Path | None:
    atlas = _atlas_cfg(config)
    if not atlas.get("db_path"):
        return None
    path = Path(str(atlas["db_path"]))
    return path if path.is_absolute() else ws.root / path


def _atlas_cfg(config: dict[str, object]) -> dict[str, object]:
    integrations = config.get("integrations")
    if not isinstance(integrations, dict):
        return {}
    atlas = integrations.get("atlas")
    return atlas if isinstance(atlas, dict) else {}


def _atlas_selectors(config: dict[str, object]) -> tuple[str | None, int | None]:
    """(repository, snapshot_id) selectors for the status probe — mirrors
    `load_structural` so the reported state matches what correlate loads."""
    atlas = _atlas_cfg(config)
    snap = atlas.get("snapshot_id")
    repo = atlas.get("repository")
    return (
        str(repo) if repo else None,
        int(str(snap)) if snap else None,
    )


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
    """Self-check: workspace, database, capabilities, integrations."""
    ws = _ws(workspace, user_scope)
    report = status_report(
        workspace_initialized=ws.root.exists(),
        db_present=ws.db_path.exists(),
    )
    report["workspace_root"] = str(ws.root)
    report["version"] = __version__
    if ws.root.exists():
        config = _load_cfg(ws)
        repo, snap = _atlas_selectors(config)
        report["integrations"] = integration_status(
            atlas_manifest=_atlas_path(config, ws),
            atlas_db=_atlas_db_path(config, ws),
            atlas_repository=repo,
            atlas_snapshot_id=snap,
        )
    from api_sentinel.operations.health import sentinel_health

    report["sentinel"] = sentinel_health(ws, now=SystemClock().now())
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
            "stage": c.stage.value,
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
    config: Annotated[
        list[str] | None,
        typer.Option(
            "--config",
            help="Source config KEY=VALUE (repeatable; e.g. region=us-east-1)",
        ),
    ] = None,
) -> None:
    """List telemetry sources, or register one with --register KIND --service-id ID."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    if register is not None:
        if not service_id:
            typer.echo("error: --service-id is required with --register", err=True)
            raise typer.Exit(EXIT_USAGE)
        cfg: dict[str, str] = {}
        for pair in config or []:
            key, sep, value = pair.partition("=")
            if not sep or not key.strip():
                raise ValidationError(
                    f"invalid --config entry: {pair!r} (expected KEY=VALUE)"
                )
            cfg[key.strip()] = value
        src = RegisterSource(uow, SystemClock(), UlidIdFactory()).execute(
            kind=register, service_id=service_id, config=cfg
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


@app.command()
def detect(
    service_id: Annotated[str, typer.Option("--service-id")],
    window_start: Annotated[
        str | None, typer.Option("--window-start", help="ISO-8601")
    ] = None,
    window_end: Annotated[
        str | None, typer.Option("--window-end", help="ISO-8601")
    ] = None,
    now: Annotated[
        str | None,
        typer.Option("--now", help="ISO-8601 evaluation instant (replay aid)"),
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Run registered detection rules for a window; dedup into incidents."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    report = RunDetection(uow, SystemClock(), UlidIdFactory()).execute(
        service_id=service_id,
        window_start=(
            _parse_dt(window_start, "--window-start") if window_start else None
        ),
        window_end=(
            _parse_dt(window_end, "--window-end") if window_end else None
        ),
        now=_parse_dt(now, "--now") if now else None,
    )
    _emit(report.model_dump(mode="json"), as_json)


@app.command("incidents")
def incidents_cmd(
    status: Annotated[
        str | None,
        typer.Option("--status", help="'open' or a concrete status"),
    ] = None,
    service_id: Annotated[str | None, typer.Option("--service-id")] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """List incidents; --status open filters to unresolved statuses."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    open_only = False
    parsed: IncidentStatus | None = None
    if status is not None:
        if status.strip().lower() == "open":
            open_only = True
        else:
            try:
                parsed = IncidentStatus(status.strip().upper())
            except ValueError as exc:
                raise ValidationError(
                    f"invalid --status: {status!r}",
                    details={
                        "expected": ["open", *sorted(s.value for s in IncidentStatus)]
                    },
                ) from exc
    out = [
        i.model_dump(mode="json")
        for i in ListIncidents(uow).execute(
            status=parsed, service_id=service_id, open_only=open_only
        )
    ]
    _emit(out, as_json)


@incident_app.command("show")
def incident_show(
    incident_id: Annotated[str, typer.Argument()],
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Incident detail: current state + journal timeline + anomalies."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    view = ShowIncident(uow).execute(incident_id=incident_id)
    _emit(view.model_dump(mode="json"), as_json)


def _incident_transition(
    incident_id: str,
    to_status: IncidentStatus,
    *,
    actor: str,
    note: str | None,
    as_json: bool,
    workspace: str | None,
    user_scope: bool,
) -> None:
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    incident = TransitionIncident(uow, SystemClock(), UlidIdFactory()).execute(
        incident_id=incident_id, to_status=to_status, actor=actor, note=note
    )
    _emit(incident.model_dump(mode="json"), as_json)


@incident_app.command("ack")
def incident_ack(
    incident_id: Annotated[str, typer.Argument()],
    actor: Annotated[str, typer.Option("--actor")] = "cli",
    note: Annotated[str | None, typer.Option("--note")] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Acknowledge: DETECTED/REOPENED → TRIAGED (journaled)."""
    _incident_transition(
        incident_id,
        IncidentStatus.TRIAGED,
        actor=actor,
        note=note,
        as_json=as_json,
        workspace=workspace,
        user_scope=user_scope,
    )


@incident_app.command("resolve")
def incident_resolve(
    incident_id: Annotated[str, typer.Argument()],
    actor: Annotated[str, typer.Option("--actor")] = "cli",
    note: Annotated[str | None, typer.Option("--note")] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Resolve: any open status → RESOLVED (journaled, sets resolved_at)."""
    _incident_transition(
        incident_id,
        IncidentStatus.RESOLVED,
        actor=actor,
        note=note,
        as_json=as_json,
        workspace=workspace,
        user_scope=user_scope,
    )


@incident_app.command("reopen")
def incident_reopen(
    incident_id: Annotated[str, typer.Argument()],
    actor: Annotated[str, typer.Option("--actor")] = "cli",
    note: Annotated[str | None, typer.Option("--note")] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Reopen: RESOLVED/DISMISSED → REOPENED (journaled only)."""
    _incident_transition(
        incident_id,
        IncidentStatus.REOPENED,
        actor=actor,
        note=note,
        as_json=as_json,
        workspace=workspace,
        user_scope=user_scope,
    )


@app.command("integrations")
def integrations_cmd(
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Every sibling Forge's honest state: available | unverified | unavailable."""
    ws = _ws(workspace, user_scope)
    config = _load_cfg(ws) if ws.root.exists() else {}
    repo, snap = _atlas_selectors(config)
    _emit(
        integration_status(
            atlas_manifest=_atlas_path(config, ws),
            atlas_db=_atlas_db_path(config, ws),
            atlas_repository=repo,
            atlas_snapshot_id=snap,
        ),
        as_json,
    )


@handoff_app.command("doctor")
def handoff_doctor(
    service_id: Annotated[str, typer.Option("--service-id")],
    target: Annotated[
        str | None,
        typer.Option("--target", help="Doctor scan root / context ref"),
    ] = None,
    capability: Annotated[
        str | None,
        typer.Option("--capability", help="Requested capability ids (CSV)"),
    ] = None,
    context_root: Annotated[
        str | None,
        typer.Option("--context-root", help="Doctor ProjectContext root (in_process)"),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="in_process | handoff_file"),
    ] = None,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Delegation budget in seconds")
    ] = 30.0,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Delegate an investigation to Forge Doctor API; audit + receipt.

    Emits the versioned `api-sentinel/InvestigationHandoff/v1` plus a
    ForgeRequest-shaped request under `handoffs/` (handoff_file) or calls
    DoctorBoundary in-process when `forge_doctor_api` is importable. The
    result is a named status — delegated | completed | refused | timed_out
    | unavailable — never a fabricated success.
    """
    from api_sentinel.integrations.doctor.adapter import DoctorACL, DoctorMode

    ws = _ws(workspace, user_scope)
    ensure_workspace(ws)
    uow = _uow(ws)
    config = _load_cfg(ws)
    acl = (
        DoctorACL(mode=DoctorMode(mode), timeout_seconds=timeout)
        if mode
        else doctor_acl_from_config(config)
    )
    if timeout != 30.0 and not mode:
        acl = DoctorACL(mode=acl.mode, timeout_seconds=timeout)
    caps = (
        [c.strip() for c in capability.split(",") if c.strip()]
        if capability else None
    )
    outcome = DelegateToDoctor(
        uow, SystemClock(), UlidIdFactory(), acl, workspace_root=ws.root
    ).execute(
        service_id=service_id,
        target=target or service_id,
        capabilities=caps,
        context_root=Path(context_root) if context_root else None,
        clock_iso=now,
    )
    _emit(outcome.model_dump(mode="json"), as_json)


@handoff_app.command("ingest")
def handoff_ingest(
    bundle: Annotated[
        Path, typer.Option("--bundle", help="Doctor-emitted bundle path")
    ],
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Ingest a Doctor-emitted findings bundle back as verbatim evidence."""
    from api_sentinel.integrations.doctor.adapter import DoctorACL

    ingest = DoctorACL(mode="handoff_file").ingest(bundle)
    _emit(ingest.model_dump(mode="json"), as_json)


@handoff_app.command("apiforge")
def handoff_apiforge(
    incident_id: Annotated[str, typer.Option("--incident-id")],
    hypothesis_id: Annotated[str, typer.Option("--hypothesis-id")],
    suggested_change: Annotated[
        str,
        typer.Option("--suggested-change", help="The fix being requested"),
    ],
    evidence_ref: Annotated[
        str | None,
        typer.Option("--evidence-ref", help="Extra evidence refs (CSV)"),
    ] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Emit an `implementation-request` to API Forge — VERIFIED only.

    The handoff carries problem + verified hypothesis + evidence refs +
    suggested change; the Sentinel requests — API Forge owns the
    implementation. A non-VERIFIED hypothesis is refused before
    transport; an absent transport is a named `unavailable`.
    """
    from api_sentinel.integrations.apiforge.adapter import (
        apiforge_from_config,
    )

    ws = _ws(workspace, user_scope)
    ensure_workspace(ws)
    uow = _uow(ws)
    config = _load_cfg(ws)
    acl = apiforge_from_config(config, ws.root)
    clock_now = _parse_dt(now, "--now") if now else SystemClock().now()
    with uow:
        incident = uow.incidents.get(incident_id)
        if incident is None:
            raise NotFoundError(f"incident not found: {incident_id}")
        hypothesis = next(
            (h for h in incident.hypotheses
             if h.hypothesis_id == hypothesis_id),
            None,
        )
        if hypothesis is None:
            raise NotFoundError(f"hypothesis not found: {hypothesis_id}")
    result = acl.implementation_request(
        incident=incident,
        hypothesis=hypothesis,
        suggested_change=suggested_change,
        evidence_refs=_csv(evidence_ref),
        at_iso=clock_now.isoformat(),
    )
    from api_sentinel.domain.models import ExternalHandoff, HandoffStatus

    status_map = {
        "delegated": HandoffStatus.DELEGATED,
        "refused": HandoffStatus.REFUSED,
        "unavailable": HandoffStatus.UNAVAILABLE,
        "failed": HandoffStatus.FAILED,
    }
    with uow:
        uow.handoff_records.put(
            ExternalHandoff(
                handoff_id=result.handoff_id,
                to_forge="api-forge",
                kind="implementation-request",
                status=status_map.get(result.status, HandoffStatus.FAILED),
                request_ref=result.request_ref,
                detail=result.detail,
                created_at=clock_now,
            )
        )
        uow.commit()
    _emit(
        {
            "handoff_id": result.handoff_id,
            "status": result.status,
            "request_ref": result.request_ref,
            "detail": result.detail,
        },
        as_json,
    )


@handoff_app.command("apiforge-ingest")
def handoff_apiforge_ingest(
    response: Annotated[
        Path,
        typer.Option("--response", help="API Forge response file path"),
    ],
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Ingest an API Forge response back as verbatim evidence."""
    from api_sentinel.integrations.apiforge.adapter import (
        ApiForgeACL,
        ApiForgeMode,
    )

    ingest = ApiForgeACL(mode=ApiForgeMode.HANDOFF_FILE).ingest(response)
    _emit(ingest.model_dump(mode="json"), as_json)


@changes_app.command("list")
def changes_list(
    service_id: Annotated[str, typer.Option("--service-id")],
    at_start: Annotated[str | None, typer.Option("--at-start")] = None,
    at_end: Annotated[str | None, typer.Option("--at-end")] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """List change events for a service."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    events = ListChanges(uow).execute(
        service_id=service_id,
        at_start=_parse_dt(at_start, "--at-start") if at_start else None,
        at_end=_parse_dt(at_end, "--at-end") if at_end else None,
    )
    _emit([e.model_dump(mode="json") for e in events], as_json)


@changes_app.command("record")
def changes_record(
    service_id: Annotated[str, typer.Option("--service-id")],
    kind: Annotated[
        str,
        typer.Option(
            "--kind",
            help="deploy|rollback|release|commit|config|flag|gateway|"
            "auth_policy|contract|dependency|maintenance",
        ),
    ],
    at: Annotated[str, typer.Option("--at", help="ISO-8601 event time")],
    ref: Annotated[str | None, typer.Option("--ref")] = None,
    source: Annotated[str, typer.Option("--source")] = "cli",
    detail: Annotated[
        str | None,
        typer.Option("--detail", help="key=value details (CSV)"),
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Record one change event — deterministic id, re-recording dedups."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    details: dict[str, str] = {}
    for pair in (detail or "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            details[k.strip()] = v.strip()
    event = RecordChange(uow, SystemClock()).execute(
        service_id=service_id,
        kind=kind,
        at=_parse_dt(at, "--at"),
        source=source,
        ref=ref,
        details=details or None,
    )
    _emit(event.model_dump(mode="json"), as_json)


@changes_app.command("import")
def changes_import(
    file: Annotated[
        Path,
        typer.Option("--file", help="DeployManifest/v1 JSON export"),
    ],
    source: Annotated[
        str | None,
        typer.Option("--source", help="Change source label (default: file)"),
    ] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Import an exported pipeline manifest — no GitHub credential needed.

    Reads `api-sentinel/DeployManifest/v1` JSON (e.g. a GitHub Actions
    export) into `change_events`; re-importing the same manifest dedups
    by deterministic change id — imported/deduplicated/skipped are
    reported honestly, never silently.
    """
    from api_sentinel.investigation.changes import ImportDeployManifest

    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    clock_now = _parse_dt(now, "--now") if now else SystemClock().now()
    report = ImportDeployManifest(uow, clock_now).execute(
        file=file, source=source
    )
    _emit(report.model_dump(mode="json"), as_json)


@changes_app.command("correlate")
def changes_correlate(
    incident_id: Annotated[str, typer.Option("--incident-id")],
    window_seconds: Annotated[
        int,
        typer.Option("--window-seconds", help="Lookback window (default 3600)"),
    ] = 3600,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Rank changes before the incident's first signal — never causal.

    Candidates are scored by temporal proximity and plausible scope with
    reasons listed; the report carries the `proximity≠cause` disclaimer —
    temporal proximity alone is never root cause.
    """
    from api_sentinel.investigation.changes import CorrelateIncidentChanges

    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    report = CorrelateIncidentChanges(uow).execute(
        incident_id=incident_id, window_seconds=window_seconds
    )
    _emit(report.model_dump(mode="json"), as_json)


@app.command()
def investigate(
    incident_id: Annotated[str, typer.Option("--incident-id")],
    principal: Annotated[
        str, typer.Option("--principal", help="Authorized session principal")
    ] = "cli",
    provider: Annotated[
        str | None, typer.Option("--provider", help="Provider selection")
    ] = None,
    model: Annotated[str | None, typer.Option("--model")] = None,
    cost_limit: Annotated[
        float | None, typer.Option("--cost-limit", help="USD budget cap")
    ] = None,
    approval: Annotated[
        str | None,
        typer.Option("--approval", help="Granted action scope (repeatable)"),
    ] = None,
    budget_bytes: Annotated[
        int, typer.Option("--budget-bytes", help="Context pack byte budget")
    ] = 65536,
    idempotency_key: Annotated[
        str | None, typer.Option("--idempotency-key")
    ] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Bounded pack → governed session request → audited investigation.

    Assembles a bounded, redacted `ContextPack` (incident, anomalies,
    journal tail, evidence refs, latest indicators, structural context,
    change correlation with proximity≠cause) and attempts a governed
    session through the Agent Bridge seam — a request, never a runtime.
    An absent Bridge is a named `unavailable`, never a simulated session;
    the deterministic investigation is persisted either way.
    """
    from api_sentinel.integrations.bridge.adapter import (
        bridge_session_from_config,
    )
    from api_sentinel.investigation.agent_session import AgentSessionPort
    from api_sentinel.investigation.service import RunAgenticInvestigation

    ws = _ws(workspace, user_scope)
    ensure_workspace(ws)
    uow = _uow(ws)
    config = _load_cfg(ws)
    session_port: AgentSessionPort = bridge_session_from_config(
        config, ws.root
    )
    structural = load_structural(config, ws.root)
    clock_now = _parse_dt(now, "--now") if now else SystemClock().now()
    approvals = (
        [a.strip() for a in approval.split(",") if a.strip()]
        if approval else []
    )
    outcome = RunAgenticInvestigation(
        uow,
        clock_now,
        UlidIdFactory(),
        session_port=session_port,
        structural=structural,
        workspace_root=ws.root,
        budget_bytes=budget_bytes,
    ).execute(
        incident_id=incident_id,
        principal=principal,
        workspace=str(ws.root),
        provider_choice=provider,
        model=model,
        cost_limit=cost_limit,
        approvals=approvals,
        idempotency_key=idempotency_key,
    )
    _emit(outcome.model_dump(mode="json"), as_json)


@hypotheses_app.command("add")
def hypotheses_add(
    incident_id: Annotated[str, typer.Option("--incident-id")],
    text: Annotated[str, typer.Option("--text")],
    confidence: Annotated[
        str,
        typer.Option("--confidence", help="CORRELATED | HYPOTHESIZED"),
    ] = "HYPOTHESIZED",
    evidence_for: Annotated[
        str | None, typer.Option("--evidence-for", help="Evidence refs (CSV)")
    ] = None,
    evidence_against: Annotated[
        str | None,
        typer.Option("--evidence-against", help="Evidence refs (CSV)"),
    ] = None,
    unknown: Annotated[
        str | None, typer.Option("--unknown", help="Unknown note (CSV)")
    ] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Attach a candidate cause — CORRELATED or HYPOTHESIZED only.

    Directional evidence and unknowns are explicit; VERIFIED is never a
    starting point — it requires an independent verification record.
    """
    from api_sentinel.investigation.hypotheses import AddHypothesis

    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    clock_now = _parse_dt(now, "--now") if now else SystemClock().now()
    hypothesis, _incident = AddHypothesis(
        uow, clock_now, UlidIdFactory()
    ).execute(
        incident_id=incident_id,
        text=text,
        confidence=confidence,
        evidence_for=_csv(evidence_for),
        evidence_against=_csv(evidence_against),
        unknowns=_csv(unknown),
        note=note,
    )
    _emit(hypothesis.model_dump(mode="json"), as_json)


@hypotheses_app.command("promote")
def hypotheses_promote(
    incident_id: Annotated[str, typer.Option("--incident-id")],
    hypothesis_id: Annotated[str, typer.Option("--hypothesis-id")],
    verification_id: Annotated[
        str,
        typer.Option(
            "--verification-id",
            help="A persisted VERIFIED verification for this hypothesis",
        ),
    ],
    note: Annotated[str | None, typer.Option("--note")] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Promote to VERIFIED — only on independent verification evidence.

    `verification_id` must name a persisted record on this incident,
    bound to the hypothesis, with verdict VERIFIED — promotion is never
    self-declared.
    """
    from api_sentinel.investigation.hypotheses import PromoteHypothesis

    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    clock_now = _parse_dt(now, "--now") if now else SystemClock().now()
    hypothesis, _incident = PromoteHypothesis(uow, clock_now).execute(
        incident_id=incident_id,
        hypothesis_id=hypothesis_id,
        verification_id=verification_id,
        note=note,
    )
    _emit(hypothesis.model_dump(mode="json"), as_json)


@hypotheses_app.command("demote")
def hypotheses_demote(
    incident_id: Annotated[str, typer.Option("--incident-id")],
    hypothesis_id: Annotated[str, typer.Option("--hypothesis-id")],
    reason: Annotated[str, typer.Option("--reason")],
    evidence_against: Annotated[
        str | None,
        typer.Option("--evidence-against", help="Evidence refs (CSV)"),
    ] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Demote to REFUTED — the terminal contradiction state, with reason."""
    from api_sentinel.investigation.hypotheses import DemoteHypothesis

    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    clock_now = _parse_dt(now, "--now") if now else SystemClock().now()
    hypothesis, _incident = DemoteHypothesis(uow, clock_now).execute(
        incident_id=incident_id,
        hypothesis_id=hypothesis_id,
        reason=reason,
        evidence_against=_csv(evidence_against),
        note=note,
    )
    _emit(hypothesis.model_dump(mode="json"), as_json)


@hypotheses_app.command("verify")
def hypotheses_verify(
    incident_id: Annotated[str, typer.Option("--incident-id")],
    hypothesis_id: Annotated[str, typer.Option("--hypothesis-id")],
    window_start: Annotated[
        str, typer.Option("--window-start", help="ISO-8601 post-change start")
    ],
    window_end: Annotated[
        str, typer.Option("--window-end", help="ISO-8601 post-change end")
    ],
    doctor: Annotated[
        bool,
        typer.Option("--doctor", help="Include a Doctor handoff step"),
    ] = False,
    now: Annotated[
        str | None, typer.Option("--now", help="ISO-8601 injected clock")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Run a falsifiable verification plan through the authorized ports.

    `re_detect` re-runs the grouping rule(s) over the post-change window —
    independent post-fix evidence; the loop closes only when the rule no
    longer fires. Verdict + step receipts persist on the incident and are
    journaled.
    """
    from api_sentinel.investigation.verification import RunVerification

    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    config = _load_cfg(ws)
    structural = load_structural(config, ws.root)
    doctor_acl = doctor_acl_from_config(config) if doctor else None
    clock_now = _parse_dt(now, "--now") if now else SystemClock().now()
    result = RunVerification(
        uow,
        clock_now,
        structural=structural,
        doctor=doctor_acl,
        workspace_root=ws.root,
    ).execute(
        incident_id=incident_id,
        hypothesis_id=hypothesis_id,
        window_start=_parse_dt(window_start, "--window-start"),
        window_end=_parse_dt(window_end, "--window-end"),
        include_doctor=doctor,
    )
    _emit(result.model_dump(mode="json"), as_json)


def _csv(raw: str | None) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


@app.command("deps")
def deps_cmd(
    service_id: Annotated[str, typer.Option("--service-id")],
    record: Annotated[
        str | None,
        typer.Option("--record", help="Observed target to record an edge for"),
    ] = None,
    window_start: Annotated[
        str | None, typer.Option("--window-start", help="ISO-8601")
    ] = None,
    window_end: Annotated[
        str | None, typer.Option("--window-end", help="ISO-8601")
    ] = None,
    target_kind: Annotated[str, typer.Option("--target-kind")] = "service",
    calls: Annotated[int, typer.Option("--calls")] = 0,
    errors: Annotated[int, typer.Option("--errors")] = 0,
    timeouts: Annotated[int, typer.Option("--timeouts")] = 0,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """List observed dependency edges, or record one with --record TARGET."""
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    if record is not None:
        if window_start is None or window_end is None:
            typer.echo(
                "error: --window-start and --window-end are required with "
                "--record", err=True,
            )
            raise typer.Exit(EXIT_USAGE)
        edge = RecordDependency(uow, SystemClock()).execute(
            service_id=service_id,
            target=record,
            target_kind=target_kind,
            window_start=_parse_dt(window_start, "--window-start"),
            window_end=_parse_dt(window_end, "--window-end"),
            calls=calls,
            errors=errors,
            timeouts=timeouts,
        )
        _emit(edge.model_dump(mode="json"), as_json)
        return
    edges = ListDependencies(uow).execute(
        service_id=service_id,
        window_start=_parse_dt(window_start, "--window-start")
        if window_start else None,
        window_end=_parse_dt(window_end, "--window-end")
        if window_end else None,
    )
    _emit([e.model_dump(mode="json") for e in edges], as_json)


@app.command()
def correlate(
    service_id: Annotated[str, typer.Option("--service-id")],
    window_start: Annotated[
        str | None, typer.Option("--window-start", help="ISO-8601")
    ] = None,
    window_end: Annotated[
        str | None, typer.Option("--window-end", help="ISO-8601")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Correlation report: observed + structural edges, honest unknowns.

    LAB-003: identifies the observed dependency and lists the structural
    context (Atlas, when configured) — hypotheses stay HYPOTHESIZED;
    temporal proximity is never root cause.
    """
    ws = _ws(workspace, user_scope)
    uow = _uow(ws)
    config = _load_cfg(ws)
    structural = load_structural(config, ws.root)
    report = Correlate(uow, structural=structural).execute(
        service_id=service_id,
        window_start=_parse_dt(window_start, "--window-start")
        if window_start else None,
        window_end=_parse_dt(window_end, "--window-end")
        if window_end else None,
    )
    _emit(report.model_dump(mode="json"), as_json)


_SIMULATE_ENV = "API_SENTINEL_AWS_SIMULATE"


def _aws_clients(
    *,
    simulate: str | None,
    session_config: AwsSessionConfig,
    now: datetime,
) -> AwsClientSet:
    """Build the read-only client set — real session or lab-only stub.

    `--simulate`/`API_SENTINEL_AWS_SIMULATE` inject the canned offline
    transport (LAB-013); they are lab-only and never perform egress.
    """
    import os

    from api_sentinel.integrations.aws.client import real_clientset
    from api_sentinel.integrations.aws.simulated import (
        SIMULATED_SCENARIOS,
        simulated_clientset,
    )

    scenario = simulate or os.environ.get(_SIMULATE_ENV)
    if scenario:
        builder = SIMULATED_SCENARIOS.get(scenario)
        if builder is None:
            raise ValidationError(
                f"unknown simulated AWS scenario: {scenario!r}",
                details={"supported": sorted(SIMULATED_SCENARIOS)},
            )
        return simulated_clientset(script_by_service=builder(now=now)).clients
    return real_clientset(session_config)


@aws_app.command("collect")
def aws_collect(
    source_id: Annotated[str, typer.Option("--source-id")],
    window: Annotated[
        str, typer.Option("--window", help="Lookback window, e.g. 30m")
    ] = "30m",
    end: Annotated[
        str | None,
        typer.Option("--end", help="ISO-8601 window end (default: now)"),
    ] = None,
    resume: Annotated[
        bool,
        typer.Option("--resume", help="Resume from the stored checkpoint"),
    ] = False,
    simulate: Annotated[
        str | None,
        typer.Option(
            "--simulate",
            help="LAB-ONLY: canned offline transport scenario "
            "(lab013 | throttle-forever); never performs real egress",
        ),
    ] = None,
    max_calls: Annotated[int | None, typer.Option("--max-calls")] = None,
    max_datapoints: Annotated[
        int | None, typer.Option("--max-datapoints")
    ] = None,
    max_lookback_minutes: Annotated[
        int | None, typer.Option("--max-lookback-minutes")
    ] = None,
    max_runtime_seconds: Annotated[
        float | None, typer.Option("--max-runtime-seconds")
    ] = None,
    max_retries: Annotated[int | None, typer.Option("--max-retries")] = None,
    max_pages: Annotated[int | None, typer.Option("--max-pages")] = None,
    max_estimated_cost_usd: Annotated[
        float | None, typer.Option("--max-estimated-cost-usd")
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Collect one window from a cloudwatch/api-gateway source.

    STS identity is verified first; every egress is budgeted; the run
    writes a redacted receipt under `receipts/` and ingests through the
    existing pipeline (idempotent — re-collecting a window dedups).
    """
    from api_sentinel.integrations.aws.budgets import AwsCollectBudget
    from api_sentinel.integrations.aws.collect import CollectAwsSource

    try:
        window_seconds = parse_duration_seconds(window)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    ws = _ws(workspace, user_scope)
    ensure_workspace(ws)
    uow = _uow(ws)
    clock = SystemClock()
    with uow:
        source = uow.sources.get(source_id)
        if source is None:
            raise NotFoundError(f"telemetry source not found: {source_id}")
    # credential-shaped config keys are refused BEFORE any session work —
    # the environment holds credentials, never Sentinel config
    session_config = AwsSessionConfig.from_source_config(source.config)
    # anchor the lab scenario at the window end so `--end` makes a
    # simulated re-collection deterministic (same window → same points
    # → pipeline dedup), never a moving wall-clock
    window_end = _parse_dt(end, "--end") if end else clock.now()
    clients = _aws_clients(
        simulate=simulate,
        session_config=session_config,
        now=window_end,
    )
    budget = AwsCollectBudget.from_source_config(
        source.config,
        overrides={
            "max_calls_per_cycle": max_calls,
            "max_datapoints": max_datapoints,
            "max_lookback_minutes": max_lookback_minutes,
            "max_runtime_seconds": max_runtime_seconds,
            "max_retries": max_retries,
            "max_pages_per_call": max_pages,
            "max_estimated_cost_usd": max_estimated_cost_usd,
        },
    )
    report = CollectAwsSource(uow, clock, UlidIdFactory()).execute(
        source=source,
        clients=clients,
        budget=budget,
        window_seconds=window_seconds,
        end=window_end if end else None,
        resume=resume,
        receipts_dir=ws.receipts_dir,
    )
    _emit(report.model_dump(mode="json"), as_json)


@aws_app.command("discover")
def aws_discover(
    profile: Annotated[
        str | None, typer.Option("--profile", help="AWS profile name")
    ] = None,
    region: Annotated[
        str | None, typer.Option("--region", help="Region scope")
    ] = None,
    account_id: Annotated[
        str | None,
        typer.Option("--account-id", help="Expected 12-digit account id"),
    ] = None,
    role_arn: Annotated[
        str | None, typer.Option("--role-arn", help="Role to assume first")
    ] = None,
    simulate: Annotated[
        str | None,
        typer.Option(
            "--simulate",
            help="LAB-ONLY: canned offline transport scenario",
        ),
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Verify identity and list REST + HTTP APIs — no collection."""
    from api_sentinel.integrations.aws.apigateway import discover_gateways
    from api_sentinel.integrations.aws.budgets import (
        AwsCollectBudget,
        BudgetMeter,
        JitteredBackoff,
    )
    from api_sentinel.integrations.aws.identities import verify_identity

    clock = SystemClock()
    session_config = AwsSessionConfig(
        profile=profile,
        region=region,
        role_arn=role_arn,
        expected_account_id=account_id,
    )
    clients = _aws_clients(
        simulate=simulate,
        session_config=session_config,
        now=clock.now(),
    )
    identity = verify_identity(
        clients.sts,
        region=clients.region,
        expected_account_id=account_id,
        expected_region=region,
    )
    budget = AwsCollectBudget()
    meter = BudgetMeter(budget)
    inventory = discover_gateways(
        clients.apigateway,
        clients.apigatewayv2,
        meter=meter,
        backoff=JitteredBackoff(),
        budget=budget,
        detailed_metrics=False,
    )
    _emit(
        {
            "account_masked": identity.masked_account,
            "account_hash": identity.account_digest,
            "region": identity.region,
            "endpoints": [e.model_dump(mode="json") for e in inventory.endpoints],
            "unknowns": inventory.unknowns,
            "calls": meter.calls,
        },
        as_json,
    )


@backup_app.command("create")
def backup_create(
    dest: Annotated[
        str | None,
        typer.Option("--dest", help="Backup parent dir (default: <ws>/backups)"),
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Verifiable workspace snapshot: db (.backup) + blobs + manifest."""
    from api_sentinel.operations.backup import create_backup

    ws = _ws(workspace, user_scope)
    result = create_backup(
        ws,
        dest_parent=Path(dest) if dest else None,
        clock=SystemClock(),
        ids=UlidIdFactory(),
    )
    _emit(
        {"backup_dir": str(result.backup_dir), "manifest": result.manifest},
        as_json,
    )


@backup_app.command("restore")
def backup_restore(
    from_dir: Annotated[
        Path, typer.Option("--from", help="Backup directory to restore")
    ],
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace a live workspace (moved aside)"),
    ] = False,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Restore a verified backup; refuses to overwrite live data w/o --force."""
    from api_sentinel.operations.backup import restore_backup

    ws = _ws(workspace, user_scope)
    result = restore_backup(
        from_dir,
        ws,
        force=force,
        clock=SystemClock(),
        ids=UlidIdFactory(),
    )
    _emit(
        {
            "backup_dir": str(result.backup_dir),
            "replaced_paths": [str(p) for p in result.replaced_paths],
            "blobs_restored": result.blobs_restored,
            "integrity": result.integrity,
        },
        as_json,
    )


@app.command()
def retention(
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Execute deletions (default: dry-run)"),
    ] = False,
    observations_days: Annotated[
        int | None,
        typer.Option("--observations-days", help="Detailed-observation tier"),
    ] = None,
    minute_days: Annotated[
        int | None,
        typer.Option("--minute-days", help="Minute-class observation tier"),
    ] = None,
    hourly_days: Annotated[
        int | None,
        typer.Option("--hourly-days", help="Hourly-class observation tier"),
    ] = None,
    quarantine_days: Annotated[
        int | None, typer.Option("--quarantine-days")
    ] = None,
    incidents_days: Annotated[
        int | None, typer.Option("--incidents-days")
    ] = None,
    evidence_days: Annotated[
        int | None, typer.Option("--evidence-days")
    ] = None,
    audit_days: Annotated[
        int | None,
        typer.Option("--audit-days", help="Opt-in audit purge (off by default)"),
    ] = None,
    as_json: _JSON = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Retention policy (docs/10, ADR-015) — dry-run by default.

    Evidence referenced by live incidents/anomalies/investigations is never
    deleted; purged-incident evidence loses protection with its incident.
    """
    from api_sentinel.operations.retention import RetentionPolicy, run_retention

    ws = _ws(workspace, user_scope)
    try:
        policy = RetentionPolicy.from_config(_load_cfg(ws))
    except Exception:
        policy = RetentionPolicy()
    policy = policy.with_overrides(
        {
            "observations_detailed_days": observations_days,
            "observations_minute_days": minute_days,
            "observations_hourly_days": hourly_days,
            "quarantine_days": quarantine_days,
            "incidents_days": incidents_days,
            "evidence_days": evidence_days,
            "audit_days": audit_days,
        }
    )
    report = run_retention(
        ws,
        apply=apply,
        policy=policy,
        clock=SystemClock(),
        ids=UlidIdFactory(),
        receipts_dir=ws.receipts_dir,
    )
    _emit(report.to_dict(), as_json)


def _is_loopback(host: str) -> bool:
    import ipaddress

    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.command()
def serve(
    host: Annotated[
        str, typer.Option("--host", help="Bind address (loopback only)")
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port")] = 8080,
    allow_remote: Annotated[
        bool,
        typer.Option(
            "--allow-remote",
            help="Permit a non-loopback bind (docs/07 requires explicit opt-in)",
        ),
    ] = False,
    token_stdin: Annotated[
        bool,
        typer.Option(
            "--token-stdin",
            help="Read the /v1 bearer token from stdin (one line) instead "
            "of the workspace token file",
        ),
    ] = False,
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Serve the local /v1 HTTP surface (docs/04) for the Forge Portal.

    Localhost by default — a non-loopback bind is refused unless
    `--allow-remote` is passed, and even then it is warned, never silent
    (docs/07: no internet exposure by default).

    Authentication (Cycle 10): every `/v1/*` route except `/v1/health`
    requires `Authorization: Bearer <token>`. The token resolves from
    `API_SENTINEL_TOKEN`, then `--token-stdin`, then `<workspace>/token`
    (generated and printed once on first serve/install — never logged).
    """
    if not _is_loopback(host):
        if not allow_remote:
            raise ValidationError(
                f"refusing non-loopback bind: {host!r} — pass --allow-remote "
                "to opt into remote exposure (docs/07)",
                details={"host": host},
            )
        typer.echo(
            f"warning: binding {host} beyond loopback — clients still need "
            "the workspace bearer token, but do not expose the /v1 surface "
            "to untrusted networks",
            err=True,
        )
    try:
        import uvicorn
    except ImportError as exc:
        raise ValidationError(
            "the 'api' extra is required to serve (fastapi/uvicorn)",
            details={"install": "pip install api-sentinel-forge[api]"},
        ) from exc
    from api_sentinel.api import create_app
    from api_sentinel.infrastructure.security.auth import resolve_auth

    ws = _ws(workspace, user_scope)
    ensure_workspace(ws)
    stdin_token = sys.stdin.readline().strip() if token_stdin else None
    auth, resolved = resolve_auth(
        ws, _load_cfg(ws), stdin_token=stdin_token
    )
    if resolved.created:
        typer.echo(
            f"generated workspace API token at {resolved.path} —\n"
            f"  Authorization: Bearer {resolved.token}\n"
            "shown once; read the token file to retrieve it later",
            err=True,
        )
    elif resolved.rotated:
        typer.echo(
            f"workspace API token rotated (ttl expired) at {resolved.path} —\n"
            f"  Authorization: Bearer {resolved.token}",
            err=True,
        )
    uvicorn.run(
        create_app(workspace=ws.root, auth=auth),
        host=host,
        port=port,
        log_level="info",
    )


mcp_app = typer.Typer(
    name="mcp",
    help="MCP server surface — read-only tools over stdio (mcp extra).",
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    workspace: _WS = None,
    user_scope: _USER_SCOPE = False,
) -> None:
    """Serve the read-only Sentinel MCP tools over stdio.

    Exposes sentinel_list_services / sentinel_capabilities /
    sentinel_health / sentinel_list_incidents to MCP-capable hosts —
    the surface `forge.agentic.json` declares via `mcp_command`.
    """
    try:
        import mcp  # noqa: F401
    except ImportError as exc:
        raise ValidationError(
            "the 'mcp' extra is required for `api-sentinel mcp serve`",
            details={"install": "pip install api-sentinel-forge[mcp]"},
        ) from exc
    from api_sentinel.mcp_server import serve_stdio

    ws = _ws(workspace, user_scope)
    ensure_workspace(ws)
    serve_stdio(ws)


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
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    sys.exit(_dispatch())
