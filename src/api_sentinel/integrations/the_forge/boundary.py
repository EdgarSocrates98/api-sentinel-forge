"""SentinelBoundary — the Sentinel's typed `forge/v1` provider seam.

Mirrors the Doctor seam shape: `describe` / `health` / `capabilities` /
`execute` over `forge/v1` Request envelopes. Every method returns plain
dicts on the public wire — the boundary is an anti-corruption layer, never
a router: a `capability`+`action` pair maps to exactly one local use case
through `_EXECUTORS`, and anything else is a named refusal.

Discovery is not verification: `describe` reports capabilities discovered
from `capabilities.py` at runtime; `execute` still refuses a `DECLARED`
capability — discovered ≠ executable (docs/06).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api_sentinel import __version__
from api_sentinel.capabilities import (
    CapabilityStatus,
    list_capabilities,
)
from api_sentinel.domain import (
    NotFoundError,
    SentinelError,
    SystemClock,
    UlidIdFactory,
    ValidationError,
    WorkspaceError,
)
from api_sentinel.integrations.errors import IntegrationRefused
from api_sentinel.integrations.the_forge.handoff import (
    EXECUTION_RESULT_SCHEMA,
    FORGE_PROTOCOL,
    THE_FORGE,
)
from api_sentinel.workspace import Workspace

PROVIDER_ID = "api-sentinel-forge"
PRODUCER = {"id": PROVIDER_ID, "version": __version__}

#: capability → declared actions (the honest routing table).
_ACTIONS: dict[str, list[str]] = {
    "api.service-inventory": ["list"],
    "api.runtime-observation": ["summarize"],
    "api.slo-assessment": ["evaluate"],
    "api.anomaly-detection": ["run"],
    "api.incident-investigation": ["list", "show"],
    "api.deployment-correlation": ["correlate"],
    "api.doctor-handoff": ["delegate"],
}

#: capability → finding class (docs/06; runtime evidence the Sentinel owns).
_FINDING_CLASS: dict[str, str] = {
    "api.runtime-observation": "runtime",
    "api.slo-assessment": "runtime",
    "api.anomaly-detection": "runtime",
    "api.incident-investigation": "runtime",
    "api.deployment-correlation": "runtime",
    "api.doctor-handoff": "runtime",
}


@dataclass
class SentinelBoundary:
    """forge/v1 Request dict in → Response dict out; never a router.

    `workspace_root` defaults to the resolved project workspace; `clock`/`ids`
    are injectable so the boundary stays deterministic under replay.
    """

    workspace_root: Path | None = None
    clock: Any = field(default_factory=SystemClock)
    ids: Any = field(default_factory=UlidIdFactory)

    # -- typed surface --------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """A `theforge/ForgeManifest/v1`-shaped manifest dict."""
        caps = []
        for cap in list_capabilities():
            actions = _ACTIONS.get(cap.capability_id, [])
            if not actions:
                # theforge/ForgeManifest/v1 requires every capability to
                # carry a default_action — a capability without a routable
                # execute pair is omitted here, never padded with a fake
                # action; it stays visible in `capabilities()`.
                continue
            caps.append(
                {
                    "id": cap.capability_id,
                    "description": cap.notes or f"Sentinel capability {cap.capability_id}",
                    "actions": actions,
                    "default_action": actions[0] if actions else None,
                    "state": (
                        "supported" if cap.status is CapabilityStatus.AVAILABLE else "heuristic"
                    ),
                    "operation_class": "read_only",
                    "signals": {"keywords": [cap.capability_id.split(".", 1)[-1]]},
                    "finding_class": _FINDING_CLASS.get(cap.capability_id, "runtime"),
                    "accepts_handoff": cap.capability_id == "api.doctor-handoff",
                }
            )
        return {
            "schema": "theforge/ForgeManifest/v1",
            "id": PROVIDER_ID,
            "version": __version__,
            "protocols": [FORGE_PROTOCOL],
            "ops": ["describe", "health", "capabilities", "execute"],
            "capabilities": caps,
            "execution": {
                "local": True,
                "offline": True,
                "requires_network": False,
                "deterministic": True,
            },
            "features": ["handoff/v1"],
            "limitations": [
                "discovery is not verification: the manifest declares only "
                "capability/action pairs with real executors; CLI-scoped "
                "capabilities without a forge execute pair are listed by "
                "the capabilities op, not routed here",
            ],
        }

    def health(self) -> dict[str, Any]:
        """`{status, checks}` — workspace/db presence, never fabricated."""
        ws = self._resolve_ws()
        db_ok = ws.db_path.exists() if ws else False
        return {
            "status": "ok" if db_ok else "degraded",
            "checks": [
                {
                    "name": "workspace_db",
                    "ok": db_ok,
                    "detail": (str(ws.db_path) if ws else "no workspace resolvable"),
                },
            ],
        }

    def capabilities(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """`theforge/SpecialistCapability/v1` discovery advertisement.

        The capabilities op answers with the *advertisement* contract — the
        same document the registry parses when it refreshes provider
        manifests. An ExecutionResult shape here is a protocol violation
        (the registry rejects `theforge/ExecutionResult/v1` for this op),
        so capabilities map to `DeclaredCapability` entries and DECLARED
        (not yet executable) ones surface as explicit limitations, never
        as findings.
        """
        declared: list[dict[str, Any]] = []
        unavailable: list[str] = []
        for cap in list_capabilities():
            declared.append(
                {
                    "id": cap.capability_id,
                    "description": cap.notes or f"Sentinel capability {cap.capability_id}",
                    "entrypoint": [],
                    "execution_mode": "local",
                    "risk": "read_only",
                    "network": False,
                    "discovery_mode": "probe",
                    "verification_mode": "probe",
                    "finding_class": _FINDING_CLASS.get(cap.capability_id, ""),
                }
            )
            if cap.status is not CapabilityStatus.AVAILABLE:
                unavailable.append(
                    f"{cap.capability_id}: {cap.status.value} — discovered, not executable"
                )
        return {
            "schema": "theforge/SpecialistCapability/v1",
            "specialist": {
                "id": PROVIDER_ID,
                "version": __version__,
                "domain": "api-reliability",
            },
            "capabilities": declared,
            "health": {"supported": True},
            "handoff": {
                "supported": any(
                    c.capability_id == "api.doctor-handoff"
                    and c.status is CapabilityStatus.AVAILABLE
                    for c in list_capabilities()
                )
            },
            "limitations": unavailable,
        }

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        """`forge/v1` execute → `theforge/ExecutionResult/v1` dict.

        The payload's `capability`/`action` select exactly one local use
        case; DECLARED capabilities and unknown actions are named refusals.
        """
        payload = request.get("payload") or {}
        capability = payload.get("capability", "")
        action = payload.get("action", "")
        cap = next(
            (c for c in list_capabilities() if c.capability_id == capability),
            None,
        )
        if cap is None:
            raise IntegrationRefused(
                THE_FORGE,
                f"unknown capability {capability!r}",
                details={"code": "APISENT-UNKNOWN-CAPABILITY"},
            )
        if cap.status is not CapabilityStatus.AVAILABLE:
            raise IntegrationRefused(
                THE_FORGE,
                f"capability {capability!r} is {cap.status.value} — discovered but not executable",
                details={"code": "APISENT-CAPABILITY-UNAVAILABLE"},
            )
        declared = _ACTIONS.get(capability, [])
        if action not in declared:
            raise IntegrationRefused(
                THE_FORGE,
                f"unknown action {action!r} for {capability!r} (declared: {sorted(declared)})",
                details={"code": "APISENT-UNKNOWN-ACTION"},
            )
        executor = _EXECUTORS.get((capability, action))
        if executor is None:
            raise IntegrationRefused(
                THE_FORGE,
                f"no executor for {capability}/{action}",
                details={"code": "APISENT-NO-EXECUTOR"},
            )
        try:
            result: dict[str, Any] = executor(self, payload)
        except NotFoundError as exc:
            raise IntegrationRefused(
                THE_FORGE,
                str(exc),
                details={"code": "APISENT-NOT-FOUND"},
            ) from exc
        except ValidationError as exc:
            raise IntegrationRefused(
                THE_FORGE,
                str(exc),
                details={"code": "APISENT-VALIDATION"},
            ) from exc
        except (WorkspaceError, sqlite3.Error, OSError) as exc:
            # e.g. no .api-sentinel under the routed workspace_root — the
            # store is unreachable, so the pair is a named refusal, never a
            # fabricated empty result nor an APISENT-INTERNAL crash.
            raise IntegrationRefused(
                THE_FORGE,
                f"workspace unavailable: {exc}",
                details={"code": "APISENT-WORKSPACE-UNAVAILABLE"},
            ) from exc
        return result

    # -- envelope gate ---------------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """The forge/v1 wire gate: Request dict in → Response dict out."""
        if request.get("kind") != "Request" or request.get("protocol") != FORGE_PROTOCOL:
            return self._respond(
                request,
                status="refused",
                error={
                    "code": "APISENT-PROTOCOL",
                    "detail": f"unsupported protocol {request.get('protocol')!r}",
                },
            )
        op = request.get("op")
        try:
            if op == "describe":
                return self._respond(request, status="ok", payload=self.describe())
            if op == "health":
                return self._respond(request, status="ok", payload=self.health())
            if op == "capabilities":
                return self._respond(request, status="ok", payload=self.capabilities(request))
            if op == "execute":
                return self._respond(request, status="ok", payload=self.execute(request))
            return self._respond(
                request,
                status="refused",
                error={
                    "code": "APISENT-UNKNOWN-OP",
                    "detail": f"unsupported op {op!r}",
                },
            )
        except IntegrationRefused as exc:
            return self._respond(
                request,
                status="refused",
                error={
                    "code": exc.details.get("code", "APISENT-REFUSED"),
                    "detail": exc.reason,
                },
            )
        except SentinelError as exc:
            return self._respond(
                request,
                status="error",
                error={"code": exc.code, "detail": exc.message},
            )
        except Exception as exc:
            return self._respond(
                request,
                status="error",
                error={"code": "APISENT-INTERNAL", "detail": str(exc)},
            )

    def _respond(
        self,
        request: dict[str, Any],
        *,
        status: str,
        payload: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "protocol": FORGE_PROTOCOL,
            "kind": "Response",
            "request_id": request.get("request_id", ""),
            "op": request.get("op"),
            "producer": PRODUCER,
            "status": status,
            "payload": payload or {},
            "limitations": [],
            "unknowns": [],
        }
        if error is not None:
            response["error"] = error
        return response

    # -- helpers ---------------------------------------------------------

    def _resolve_ws(self, payload: dict[str, Any] | None = None) -> Workspace:
        """Resolve the Sentinel workspace for this request.

        Precedence: explicit `workspace_root` on the boundary > the routed
        task's `workspace_root` (the-forge spawns providers under
        `.forge/runs/<id>/work/` — the run dir, never the project — so the
        target workspace travels in the payload) > `context.root` >
        env/cwd fallback. The Sentinel workspace is `<root>/.api-sentinel`.
        """
        from api_sentinel.workspace import resolve_workspace

        if self.workspace_root is not None:
            return resolve_workspace(explicit=Path(self.workspace_root))
        if payload:
            task = payload.get("task") or {}
            context = payload.get("context") or {}
            for root in (task.get("workspace_root"), context.get("root")):
                if root and (Path(str(root)) / ".api-sentinel").is_dir():
                    return resolve_workspace(explicit=Path(str(root)) / ".api-sentinel")
        return resolve_workspace()

    def _result(
        self,
        *,
        status: str,
        findings: list[dict[str, Any]] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        limitations: list[str] | None = None,
        unknowns: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": EXECUTION_RESULT_SCHEMA,
            "producer": PRODUCER,
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": status,
            "findings": findings or [],
            "evidence": evidence or [],
            "artifacts": [],
            "limitations": limitations or [],
            "unknowns": unknowns or [],
        }


# --- local use-case executors -------------------------------------------------

Executor = Callable[["SentinelBoundary", dict[str, Any]], dict[str, Any]]


def _exec_service_inventory(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    from api_sentinel.application.use_cases import ListServices
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    ws = self._resolve_ws(payload)
    # ListServices opens/commits its own `with uow:` — passing the UoW lets
    # the use case own the transaction (a nested BEGIN is an error).
    services = ListServices(SqliteUnitOfWork(ws.db_path)).execute()
    return self._result(
        status="ok" if services else "partial",
        findings=[
            {
                "id": s.service_id,
                "title": f"service {s.service_name}",
                "severity": "info",
                "evidence_ids": [f"svc-row:{s.service_id}"],
            }
            for s in services
        ],
        evidence=[
            {
                "id": f"svc-row:{s.service_id}",
                "epistemic": "observed",
                "subject": s.service_id,
                "claim": (f"persisted service row {s.service_name} env={s.environment_id}"),
                "producer": PRODUCER,
            }
            for s in services
        ],
        unknowns=[] if services else ["no services registered in workspace"],
    )


def _exec_runtime_observation(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    service_id = _param(payload, "service_id")
    ws = self._resolve_ws(payload)
    with SqliteUnitOfWork(ws.db_path) as uow:
        if service_id:
            obs = uow.observations.list_for_service(service_id)
        else:
            # unscoped summarize → aggregate across every registered service
            obs = [
                o
                for svc in uow.services.list()
                for o in uow.observations.list_for_service(svc.service_id)
            ]
        aggregates = uow.observations.aggregates()
    return self._result(
        status="ok" if obs else "partial",
        findings=[
            {
                "id": "observations",
                "title": f"{len(obs)} observations"
                + (f" for {service_id}" if service_id else " (all services)"),
                "severity": "info",
                "evidence_ids": [],
            }
        ],
        evidence=[
            {
                "id": "obs-count",
                "epistemic": "observed",
                "subject": service_id or "workspace",
                "claim": f"observation aggregates: {aggregates}",
                "producer": PRODUCER,
            }
        ],
        unknowns=[] if obs else ["no observations in the requested scope"],
    )


def _param(payload: dict[str, Any], name: str) -> Any:
    task = payload.get("task") or {}
    constraints = task.get("constraints") or {}
    return constraints.get(name) or payload.get(name)


#: Sentinel severity ladder → theforge/ExecutionResult/v1 severity wire
#: vocabulary (info|low|medium|high|critical). The mapping is mechanical,
#: never inferred from status codes.
_SEVERITY_WIRE: dict[str, str] = {
    "SEV1": "critical",
    "SEV2": "high",
    "SEV3": "medium",
    "SEV4": "low",
    "INFORMATIONAL": "info",
}


def _wire_severity(value: Any) -> str:
    return _SEVERITY_WIRE.get(str(value), "info")


def _ref_evidence(refs: list[str], *, subject: str, claim: str) -> list[dict[str, Any]]:
    """Sentinel-side evidence refs → theforge Evidence records.

    theforge/ExecutionResult/v1 enforces that every finding evidence_id is
    declared in the result's own evidence list (dangling refs are a real
    contract rejection — C10 evidence). The refs stay verbatim: `obs:…`,
    `src:…`, `inc:…` and artifact paths are the persisted workspace ids.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(
            {
                "id": ref,
                "epistemic": "observed",
                "subject": subject,
                "claim": f"{claim} (ref {ref})",
                "producer": PRODUCER,
            }
        )
    return out


def _require(payload: dict[str, Any], name: str) -> Any:
    """A required execute parameter — absent is a named refusal."""
    value = _param(payload, name)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise IntegrationRefused(
            THE_FORGE,
            f"execute requires parameter {name!r} (task.constraints or payload)",
            details={"code": "APISENT-MISSING-PARAM"},
        )
    return value


def _service_scope(payload: dict[str, Any], ws: Workspace) -> tuple[str | None, str | None]:
    """Resolve the service scope for service-scoped execute pairs.

    `service_id` in task.constraints wins. Absent that — the forge/v1 wire
    carries no parameter channel — a workspace with exactly one registered
    service is unambiguous scope; zero or many is a named unknown, never a
    name guess.
    """
    service_id = _param(payload, "service_id")
    if service_id:
        return str(service_id), None
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    with SqliteUnitOfWork(ws.db_path) as uow:
        ids = [s.service_id for s in uow.services.list()]
    if len(ids) == 1:
        return ids[0], None
    if not ids:
        raise IntegrationRefused(
            THE_FORGE,
            "execute requires parameter 'service_id' — the workspace registers no services",
            details={"code": "APISENT-MISSING-PARAM"},
        )
    return None, (
        f"service_id required: {len(ids)} services registered "
        f"({', '.join(sorted(ids))}) — the forge/v1 wire carries no "
        "parameter channel; scope is ambiguous, never guessed"
    )


def _window(payload: dict[str, Any]) -> tuple[Any, Any]:
    """Optional ISO-8601 window bounds from task constraints/payload."""
    from datetime import datetime

    def _parse(value: Any, name: str) -> Any:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError as exc:
            raise IntegrationRefused(
                THE_FORGE,
                f"invalid ISO-8601 {name}: {value!r}",
                details={"code": "APISENT-BAD-PARAM"},
            ) from exc

    return (
        _parse(_param(payload, "window_start"), "window_start"),
        _parse(_param(payload, "window_end"), "window_end"),
    )


def _exec_slo_assessment(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    """`api.slo-assessment/evaluate` → EvaluateSlo over real definitions.

    `slo_id` selects one definition (optional `revision`); without it every
    definition in scope is evaluated. The window comes from task
    constraints (`window_start`/`window_end` ISO-8601) or, absent that,
    from the stored observation range — the same derivation the CLI uses.
    """
    from api_sentinel.application.use_cases import EvaluateSlo, ListSloDefinitions
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    service_id = _param(payload, "service_id")
    slo_id = _param(payload, "slo_id")
    revision = _param(payload, "revision")
    window_start, window_end = _window(payload)
    ws = self._resolve_ws(payload)
    uow = SqliteUnitOfWork(ws.db_path)
    definitions = ListSloDefinitions(uow).execute(service_id=service_id)
    if slo_id:
        definitions = [d for d in definitions if d.slo_id == slo_id]
    if not definitions:
        return self._result(
            status="partial",
            unknowns=[
                "no SLO definitions in scope"
                + (f" (service_id={service_id})" if service_id else "")
                + (f" (slo_id={slo_id})" if slo_id else "")
            ],
        )
    findings: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    unknowns: list[str] = []
    evaluated = 0
    for definition in sorted(definitions, key=lambda d: (d.slo_id, d.revision)):
        start, end = window_start, window_end
        if start is None or end is None:
            with uow:
                obs = uow.observations.list_for_service(definition.service_id)
            if not obs:
                unknowns.append(
                    f"{definition.slo_id}@{definition.revision}: no stored "
                    "observations and no explicit window — not evaluated"
                )
                continue
            start = start or min(o.window_start for o in obs)
            end = end or max(o.window_end for o in obs)
        assessment = EvaluateSlo(uow, self.clock, self.ids).execute(
            slo_id=definition.slo_id,
            window_start=start,
            window_end=end,
            revision=int(revision) if revision else None,
        )
        evaluated += 1
        sli = f"{assessment.sli:.4f}" if assessment.sli is not None else "n/a"
        ev_id = f"slo-assessment:{assessment.assessment_id}"
        evidence.append(
            {
                "id": ev_id,
                "epistemic": "observed",
                "subject": definition.slo_id,
                "claim": (
                    f"persisted assessment {assessment.assessment_id} "
                    f"rule={assessment.rule_id}@{assessment.rule_version} "
                    f"quality={assessment.quality.value}"
                ),
                "producer": PRODUCER,
            }
        )
        findings.append(
            {
                "id": f"{definition.slo_id}@{definition.revision}",
                "title": (
                    f"SLO {definition.slo_id} rev {definition.revision}: "
                    f"{assessment.state.value} "
                    f"(sli={sli} target={definition.target})"
                ),
                "severity": ("info" if assessment.state.value == "MET" else "high"),
                "evidence_ids": [ev_id],
            }
        )
    status = "ok" if evaluated == len(definitions) else "partial"
    return self._result(status=status, findings=findings, evidence=evidence, unknowns=unknowns)


def _exec_anomaly_detection(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    """`api.anomaly-detection/run` → RunDetection over a real service."""
    from api_sentinel.application.use_cases import RunDetection
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    ws = self._resolve_ws(payload)
    service_id, scope_unknown = _service_scope(payload, ws)
    if service_id is None:
        return self._result(status="partial", unknowns=[scope_unknown or ""])
    window_start, window_end = _window(payload)
    report = RunDetection(SqliteUnitOfWork(ws.db_path), self.clock, self.ids).execute(
        service_id=service_id,
        window_start=window_start,
        window_end=window_end,
    )
    anomalies = report.anomalies
    return self._result(
        status="ok" if anomalies else "partial",
        findings=[
            {
                "id": a.anomaly_id,
                "title": (f"{a.rule.detector_id} on {a.service_id}: {a.severity.value}"),
                "severity": _wire_severity(a.severity.value),
                "evidence_ids": list(a.evidence_refs),
            }
            for a in anomalies
        ],
        evidence=[
            {
                "id": f"run-{report.request_id}",
                "epistemic": "observed",
                "subject": service_id,
                "claim": (
                    f"detection run {report.request_id}: "
                    f"{len(report.evaluations)} evaluations, "
                    f"{len(anomalies)} anomalies, "
                    f"{len(report.deduplicated)} deduplicated, "
                    f"{len(report.incidents_created)} incidents created"
                ),
                "producer": PRODUCER,
            },
            *_ref_evidence(
                [r for a in anomalies for r in a.evidence_refs],
                subject=service_id,
                claim="observation/source row persisted in the workspace",
            ),
        ],
        unknowns=[]
        if anomalies
        else [
            "no new anomalies in the requested window"
            + (f" ({len(report.deduplicated)} deduplicated)" if report.deduplicated else "")
        ],
    )


def _exec_incident_list(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    """`api.incident-investigation/list` → ListIncidents (scoped, honest)."""
    from api_sentinel.application.use_cases import ListIncidents
    from api_sentinel.domain import ValidationError
    from api_sentinel.incidents import IncidentStatus
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    service_id = _param(payload, "service_id")
    open_only = bool(_param(payload, "open_only"))
    raw_status = _param(payload, "status")
    status = None
    if raw_status:
        try:
            status = IncidentStatus(str(raw_status))
        except ValueError as exc:
            raise ValidationError(f"unknown incident status {raw_status!r}") from exc
    ws = self._resolve_ws(payload)
    incidents = ListIncidents(SqliteUnitOfWork(ws.db_path)).execute(
        status=status, service_id=service_id, open_only=open_only
    )
    return self._result(
        status="ok" if incidents else "partial",
        findings=[
            {
                "id": i.incident_id,
                "title": f"{i.title} [{i.status.value}]",
                "severity": _wire_severity(i.severity.value),
                "evidence_ids": list(i.evidence_refs),
            }
            for i in incidents
        ],
        evidence=_ref_evidence(
            [r for i in incidents for r in i.evidence_refs],
            subject=service_id or "workspace",
            claim="incident evidence row persisted in the workspace",
        ),
        unknowns=[] if incidents else ["no incidents in the requested scope"],
    )


def _exec_incident_show(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    """`api.incident-investigation/show` → ShowIncident + journal summary."""
    from api_sentinel.application.use_cases import ShowIncident
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    incident_id = _require(payload, "incident_id")
    ws = self._resolve_ws(payload)
    view = ShowIncident(SqliteUnitOfWork(ws.db_path)).execute(incident_id=incident_id)
    i = view.incident
    return self._result(
        status="ok",
        findings=[
            {
                "id": i.incident_id,
                "title": f"{i.title} [{i.status.value}]",
                "severity": _wire_severity(i.severity.value),
                "evidence_ids": list(i.evidence_refs),
            }
        ],
        evidence=[
            {
                "id": f"journal-{i.incident_id}",
                "epistemic": "observed",
                "subject": i.incident_id,
                "claim": (
                    f"{len(view.events)} journal events, "
                    f"{len(view.anomalies)} linked anomalies, "
                    f"{len(i.hypotheses)} hypotheses"
                ),
                "producer": PRODUCER,
            },
            *_ref_evidence(
                list(i.evidence_refs),
                subject=i.incident_id,
                claim="incident evidence row persisted in the workspace",
            ),
        ],
        unknowns=[] if view.events else ["incident has no journal events"],
    )


def _exec_deployment_correlation(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    """`api.deployment-correlation/correlate` → the LAB-003 report.

    Observed edges, structural context (when Atlas is wired in the
    workspace config) and honest unknowns — the same report the CLI
    produces, never causal certainty.
    """
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork
    from api_sentinel.integrations.service import Correlate, load_structural
    from api_sentinel.workspace import load_config

    ws = self._resolve_ws(payload)
    service_id, scope_unknown = _service_scope(payload, ws)
    if service_id is None:
        return self._result(status="partial", unknowns=[scope_unknown or ""])
    window_start, window_end = _window(payload)
    config: dict[str, object] = {}
    try:
        config = load_config(ws)
    except Exception:
        config = {}
    structural = load_structural(config, ws.root) if config else None
    report = Correlate(
        SqliteUnitOfWork(ws.db_path),
        structural=structural,
        clock=self.clock,
    ).execute(
        service_id=service_id,
        window_start=window_start,
        window_end=window_end,
    )
    edge_findings = [
        {
            "id": f"obs-{idx}",
            "title": f"observed {e.from_ref} -> {e.to_ref} ({e.evidence})",
            "severity": "info",
            "evidence_ids": [],
        }
        for idx, e in enumerate(report.observed_edges)
    ] + [
        {
            "id": f"str-{idx}",
            "title": f"structural {e.from_ref} -> {e.to_ref} ({e.evidence})",
            "severity": "info",
            "evidence_ids": [],
        }
        for idx, e in enumerate(report.structural_edges)
    ]
    if report.suspected_origin:
        edge_findings.append(
            {
                "id": "suspected-origin",
                "title": (f"suspected origin (observed, not causal): {report.suspected_origin}"),
                "severity": "medium",
                "evidence_ids": [],
            }
        )
    return self._result(
        status="ok" if edge_findings else "partial",
        findings=edge_findings,
        limitations=[
            "correlation is not causation — observed and structural edges stay distinct (docs/06)"
        ],
        unknowns=list(report.unknowns),
    )


def _exec_doctor_handoff(self: SentinelBoundary, payload: dict[str, Any]) -> dict[str, Any]:
    """`api.doctor-handoff/delegate` → DelegateToDoctor (real or named)."""
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork
    from api_sentinel.integrations.service import (
        DelegateToDoctor,
        doctor_acl_from_config,
    )
    from api_sentinel.workspace import load_config

    ws = self._resolve_ws(payload)
    service_id, scope_unknown = _service_scope(payload, ws)
    if service_id is None:
        return self._result(status="partial", unknowns=[scope_unknown or ""])
    target = _param(payload, "target") or service_id
    context_root = _param(payload, "context_root")
    config: dict[str, object] = {}
    try:
        config = load_config(ws)
    except Exception:
        config = {}
    acl = doctor_acl_from_config(config)
    outcome = DelegateToDoctor(
        SqliteUnitOfWork(ws.db_path),
        self.clock,
        self.ids,
        acl,
        workspace_root=ws.root,
    ).execute(
        service_id=service_id,
        target=target,
        context_root=Path(context_root) if context_root else None,
    )
    ingest = outcome.ingest
    handoff_id = outcome.handoff.handoff_id
    return self._result(
        status="ok" if outcome.status == "completed" else "partial",
        findings=[
            {
                "id": handoff_id,
                "title": f"doctor handoff {outcome.status} ({outcome.mode})",
                "severity": "info",
                "evidence_ids": ([outcome.request_ref] if outcome.request_ref else []),
            }
        ],
        evidence=(
            [
                {
                    "id": f"doctor-ingest-{handoff_id}",
                    "epistemic": "observed",
                    "subject": target,
                    "claim": (
                        f"doctor bundle: {len(ingest.findings)} findings, "
                        f"{len(ingest.unknowns)} unknowns, "
                        f"analysis_rev={ingest.analysis_rev or 'n/a'}"
                    ),
                    "producer": PRODUCER,
                }
            ]
            if ingest is not None
            else []
        )
        + _ref_evidence(
            [outcome.request_ref] if outcome.request_ref else [],
            subject=target,
            claim="persisted handoff request envelope",
        ),
        limitations=[] if outcome.status == "completed" else [outcome.detail],
        unknowns=list(outcome.unknowns),
    )


_EXECUTORS: dict[tuple[str, str], Executor] = {
    ("api.service-inventory", "list"): _exec_service_inventory,
    ("api.runtime-observation", "summarize"): _exec_runtime_observation,
    ("api.slo-assessment", "evaluate"): _exec_slo_assessment,
    ("api.anomaly-detection", "run"): _exec_anomaly_detection,
    ("api.incident-investigation", "list"): _exec_incident_list,
    ("api.incident-investigation", "show"): _exec_incident_show,
    ("api.deployment-correlation", "correlate"): _exec_deployment_correlation,
    ("api.doctor-handoff", "delegate"): _exec_doctor_handoff,
}
