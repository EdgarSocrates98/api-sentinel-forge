"""Read-side data for the TUI panels — honest when the workspace is absent.

Every loader returns `PanelData(rows, note)`; a missing workspace or
database produces a named `note` (unavailable / not initialized) — the
panels render the named state rather than fabricating empty health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api_sentinel.domain import SystemClock
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.workspace import Workspace, ensure_workspace, resolve_workspace


@dataclass
class PanelData:
    columns: list[str] = field(default_factory=list)
    rows: list[tuple[str, ...]] = field(default_factory=list)
    note: str = ""


def resolve_ws(explicit: str | None, user_scope: bool) -> Workspace:
    return resolve_workspace(
        scope="user" if user_scope else "project",
        explicit=Path(explicit) if explicit else None,
    )


def _uow(ws: Workspace) -> SqliteUnitOfWork | None:
    if not ws.db_path.is_file():
        return None
    ensure_workspace(ws)
    return SqliteUnitOfWork(ws.db_path)


def load_services(ws: Workspace) -> PanelData:
    uow = _uow(ws)
    if uow is None:
        return PanelData(note=f"unavailable — no database at {ws.db_path}")
    with uow:
        services = uow.services.list()
        sources = uow.sources.list()
    by_svc: dict[str, list[str]] = {}
    for s in sources:
        by_svc.setdefault(s.service_id, []).append(
            f"{s.source_id}({s.kind}{'' if s.enabled else ',off'})"
        )
    return PanelData(
        columns=["service", "name", "env", "sources", "registered"],
        rows=[
            (
                svc.service_id,
                svc.service_name,
                svc.environment_id,
                ", ".join(by_svc.get(svc.service_id, [])) or "—",
                str(svc.created_at),
            )
            for svc in services
        ],
        note="" if services else "no services registered",
    )


def load_incidents(ws: Workspace) -> PanelData:
    uow = _uow(ws)
    if uow is None:
        return PanelData(note="unavailable — no database")
    with uow:
        inc = uow.incidents.list()
    return PanelData(
        columns=["incident", "status", "severity", "first observed", "group"],
        rows=[
            (
                i.incident_id,
                i.status.value,
                i.severity.value,
                str(i.first_observed_at),
                i.group_key,
            )
            for i in inc
        ],
        note="" if inc else "no incidents — nothing detected (≠ healthy)",
    )


def load_slos(ws: Workspace) -> PanelData:
    uow = _uow(ws)
    if uow is None:
        return PanelData(note="unavailable — no database")
    with uow:
        defs = uow.slo_definitions.list()
        rows: list[tuple[str, ...]] = []
        for d in defs:
            history = uow.slo_assessments.list(d.slo_id, d.revision)
            latest = max(history, key=lambda a: a.evaluated_at) if history else None
            state = latest.state.value if latest else "not_evaluated"
            sli = f"{latest.sli:.4f}" if latest and latest.sli is not None else "—"
            lims = ",".join(latest.limitations[:2]) if latest else ""
            rows.append(
                (
                    f"{d.slo_id}@r{d.revision}",
                    d.service_id,
                    f"{d.target:.3f}",
                    state,
                    sli,
                    lims,
                )
            )
    return PanelData(
        columns=["slo@rev", "service", "target", "latest state", "sli", "limitations"],
        rows=rows,
        note="" if rows else "no SLO definitions",
    )


def load_health(ws: Workspace) -> PanelData:
    from api_sentinel.application.use_cases import GetServiceHealth

    uow = _uow(ws)
    if uow is None:
        return PanelData(note="unavailable — no database")
    rows: list[tuple[str, ...]] = []
    with uow:
        services = uow.services.list()
    for svc in services:
        try:
            rep = GetServiceHealth(uow, SystemClock()).execute(service_id=svc.service_id)
            rows.append(
                (
                    svc.service_id,
                    rep.state.value,
                    ",".join(rep.unknowns[:2]) if rep.unknowns else "—",
                )
            )
        except Exception as exc:  # named, never hidden
            rows.append((svc.service_id, "error", type(exc).__name__))
    return PanelData(
        columns=["service", "health", "unknowns"],
        rows=rows,
        note="" if rows else "no services — nothing to assess",
    )


def load_integrations(ws: Workspace) -> PanelData:
    from api_sentinel.integrations.status import integration_status
    from api_sentinel.workspace import load_config

    try:
        config: dict[str, Any] = load_config(ws)
    except Exception:
        config = {}
    integrations = config.get("integrations")
    atlas = integrations.get("atlas") if isinstance(integrations, dict) else None
    atlas = atlas if isinstance(atlas, dict) else {}

    def _resolve(key: str) -> Path | None:
        if not atlas.get(key):
            return None
        p = Path(str(atlas[key]))
        return p if p.is_absolute() else ws.root / p

    try:
        report = integration_status(
            atlas_manifest=_resolve("export"),
            atlas_db=_resolve("db_path"),
            atlas_repository=(str(atlas["repository"]) if atlas.get("repository") else None),
            atlas_snapshot_id=(
                int(str(atlas["snapshot_id"])) if atlas.get("snapshot_id") else None
            ),
        )
    except Exception as exc:
        return PanelData(note=f"unavailable — {type(exc).__name__}: {exc}")
    rows: list[tuple[str, ...]] = [
        (str(d.get("forge", "?")), str(d.get("status", "?")), str(d.get("detail", ""))[:60])
        for d in report
    ]
    return PanelData(columns=["forge", "status", "detail"], rows=rows)


def load_audit(ws: Workspace, limit: int = 15) -> PanelData:
    uow = _uow(ws)
    if uow is None:
        return PanelData(note="unavailable — no database")
    with uow:
        events = list(uow.audit.list())[-limit:]
    return PanelData(
        columns=["at", "action", "target"],
        rows=[(str(e.at), e.action, e.target_id) for e in events],
        note="" if events else "no audit events",
    )
