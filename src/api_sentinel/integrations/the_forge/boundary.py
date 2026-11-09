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
    SentinelError,
    SystemClock,
    UlidIdFactory,
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
    "api.incident-investigation": ["list"],
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
            caps.append({
                "id": cap.capability_id,
                "description": cap.notes or f"Sentinel capability {cap.capability_id}",
                "actions": actions,
                "default_action": actions[0] if actions else None,
                "state": (
                    "supported" if cap.status is CapabilityStatus.AVAILABLE
                    else "heuristic"
                ),
                "operation_class": "read_only",
                "signals": {"keywords": [cap.capability_id.split(".", 1)[-1]]},
                "finding_class": _FINDING_CLASS.get(cap.capability_id, "runtime"),
                "accepts_handoff": cap.capability_id == "api.doctor-handoff",
            })
        return {
            "schema": "theforge/ForgeManifest/v1",
            "id": PROVIDER_ID,
            "version": __version__,
            "protocols": [FORGE_PROTOCOL],
            "ops": ["describe", "health", "capabilities", "execute"],
            "capabilities": caps,
            "execution": {
                "local": True, "offline": True,
                "requires_network": False, "deterministic": True,
            },
            "features": ["handoff/v1"],
            "limitations": [
                "discovery is not verification: declared capabilities are "
                "listed but refused at execute until evidence supports them",
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
                    "detail": (
                        str(ws.db_path) if ws
                        else "no workspace resolvable"
                    ),
                },
            ],
        }

    def capabilities(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """ExecutionResult-shaped capability report (runtime discovery)."""
        caps = list_capabilities()
        payload = self._result(
            status="ok",
            findings=[
                {
                    "id": c.capability_id,
                    "title": f"{c.capability_id}: {c.status.value}",
                    "severity": "info",
                    "evidence_ids": [],
                }
                for c in caps
            ],
            unknowns=[
                f"{c.capability_id}: declared, not yet evidence-backed"
                for c in caps if c.status is CapabilityStatus.DECLARED
            ],
        )
        return payload

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
                THE_FORGE, f"unknown capability {capability!r}",
                details={"code": "APISENT-UNKNOWN-CAPABILITY"},
            )
        if cap.status is not CapabilityStatus.AVAILABLE:
            raise IntegrationRefused(
                THE_FORGE,
                f"capability {capability!r} is {cap.status.value} — "
                "discovered but not executable",
                details={"code": "APISENT-CAPABILITY-UNAVAILABLE"},
            )
        declared = _ACTIONS.get(capability, [])
        if action not in declared:
            raise IntegrationRefused(
                THE_FORGE,
                f"unknown action {action!r} for {capability!r} "
                f"(declared: {sorted(declared)})",
                details={"code": "APISENT-UNKNOWN-ACTION"},
            )
        executor = _EXECUTORS.get((capability, action))
        if executor is None:
            raise IntegrationRefused(
                THE_FORGE,
                f"no executor for {capability}/{action}",
                details={"code": "APISENT-NO-EXECUTOR"},
            )
        result: dict[str, Any] = executor(self, payload)
        return result

    # -- envelope gate ---------------------------------------------------

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        """The forge/v1 wire gate: Request dict in → Response dict out."""
        if request.get("kind") != "Request" or request.get("protocol") != FORGE_PROTOCOL:
            return self._respond(
                request, status="refused",
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
                return self._respond(
                    request, status="ok", payload=self.capabilities(request)
                )
            if op == "execute":
                return self._respond(
                    request, status="ok", payload=self.execute(request)
                )
            return self._respond(
                request, status="refused",
                error={
                    "code": "APISENT-UNKNOWN-OP",
                    "detail": f"unsupported op {op!r}",
                },
            )
        except IntegrationRefused as exc:
            return self._respond(
                request, status="refused",
                error={
                    "code": exc.details.get("code", "APISENT-REFUSED"),
                    "detail": exc.reason,
                },
            )
        except SentinelError as exc:
            return self._respond(
                request, status="error",
                error={"code": exc.code, "detail": exc.message},
            )
        except Exception as exc:
            return self._respond(
                request, status="error",
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

    def _resolve_ws(self) -> Workspace:
        from api_sentinel.workspace import resolve_workspace

        if self.workspace_root is not None:
            return resolve_workspace(explicit=Path(self.workspace_root))
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
            "created_at": datetime.now(UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "status": status,
            "findings": findings or [],
            "evidence": evidence or [],
            "artifacts": [],
            "limitations": limitations or [],
            "unknowns": unknowns or [],
        }


# --- local use-case executors -------------------------------------------------

Executor = Callable[["SentinelBoundary", dict[str, Any]], dict[str, Any]]


def _exec_service_inventory(
    self: SentinelBoundary, payload: dict[str, Any]
) -> dict[str, Any]:
    from api_sentinel.application.use_cases import ListServices
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    ws = self._resolve_ws()
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
                "evidence_ids": [],
            }
            for s in services
        ],
        unknowns=[] if services else ["no services registered in workspace"],
    )


def _exec_runtime_observation(
    self: SentinelBoundary, payload: dict[str, Any]
) -> dict[str, Any]:
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    service_id = _param(payload, "service_id")
    ws = self._resolve_ws()
    with SqliteUnitOfWork(ws.db_path) as uow:
        obs = (
            uow.observations.list_for_service(service_id) if service_id
            else []
        )
        aggregates = uow.observations.aggregates()
    return self._result(
        status="ok" if obs else "partial",
        findings=[
            {
                "id": "observations",
                "title": f"{len(obs)} observations"
                         + (f" for {service_id}" if service_id else ""),
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


_EXECUTORS: dict[tuple[str, str], Executor] = {
    ("api.service-inventory", "list"): _exec_service_inventory,
    ("api.runtime-observation", "summarize"): _exec_runtime_observation,
}
