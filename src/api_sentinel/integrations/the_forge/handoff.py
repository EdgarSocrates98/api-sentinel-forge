"""InvestigationHandoff/v1 ↔ forge/v1 envelope translation — version-strict.

Outbound: the Sentinel handoff becomes a `forge/v1` `Request` with
`op="execute"` — `payload.capability`/`action`/`task`/`context` come from the
handoff payload verbatim (never reinterpreted); `payload.handoff` carries the
envelope reference the receiving provider resolves.

Inbound: a `forge/v1` `Response` is checked strictly — wrong protocol, wrong
kind or a refused/error response raises a named `IntegrationRefused` /
`IntegrationUnavailable`, never a silent default. A valid ok/partial response
returns its `theforge/ExecutionResult/v1`-shaped payload dict.
"""

from __future__ import annotations

from typing import Any

from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.domain import ContractError
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

THE_FORGE = "the-forge"
FORGE_PROTOCOL = "forge/v1"
EXECUTION_RESULT_SCHEMA = "theforge/ExecutionResult/v1"


def handoff_to_forge_request(handoff: InvestigationHandoffV1) -> dict[str, Any]:
    """Translate a v1 handoff into a `forge/v1` `Request` envelope dict.

    Strict: only `api-sentinel/InvestigationHandoff/v1` is accepted — any
    other schema is a named `IntegrationRefused` (ADR-006: unknown versions
    rejected, never defaulted). `payload.capability` and `payload.action`
    must be present — The Forge routes on them.
    """
    if handoff.schema_ != InvestigationHandoffV1.expected_schema:
        raise IntegrationRefused(
            THE_FORGE,
            f"unsupported handoff schema '{handoff.schema_}'",
            details={
                "received": handoff.schema_,
                "expected": InvestigationHandoffV1.expected_schema,
            },
        )
    capability = handoff.payload.get("capability")
    action = handoff.payload.get("action")
    if not capability or not action:
        raise IntegrationRefused(
            THE_FORGE,
            "handoff payload must declare 'capability' and 'action' for "
            "the forge/v1 execute envelope",
        )
    return {
        "protocol": FORGE_PROTOCOL,
        "kind": "Request",
        "request_id": handoff.handoff_id,
        "op": "execute",
        "payload": {
            "task": handoff.payload.get("task", {}),
            "capability": capability,
            "action": action,
            "context": handoff.payload.get("context", {}),
            "handoff": {
                "schema": handoff.schema_,
                "handoff_id": handoff.handoff_id,
                "from_forge": handoff.from_forge,
                "to_forge": handoff.to_forge,
                "kind": handoff.kind,
                "evidence_refs": list(handoff.constraints.evidence_refs),
            },
            **({"delta": handoff.payload["delta"]} if "delta" in handoff.payload else {}),
        },
    }


def response_to_result(response: dict[str, Any]) -> dict[str, Any]:
    """Validate a `forge/v1` `Response` and return its result payload.

    `ok`/`partial` return the `theforge/ExecutionResult/v1` payload dict
    (schema-checked when present); `refused`/`error` raise named errors —
    a refused delegation is surfaced, never swallowed.
    """
    if not isinstance(response, dict):
        raise IntegrationUnavailable(THE_FORGE, "response is not a JSON object")
    if response.get("protocol") != FORGE_PROTOCOL:
        raise IntegrationRefused(
            THE_FORGE,
            f"unsupported response protocol {response.get('protocol')!r}",
            details={"expected": FORGE_PROTOCOL},
        )
    if response.get("kind") != "Response":
        raise IntegrationRefused(
            THE_FORGE, f"expected kind 'Response', got {response.get('kind')!r}"
        )
    status = response.get("status")
    error = response.get("error") or {}
    if status == "refused":
        raise IntegrationRefused(
            THE_FORGE,
            error.get("detail") or "request refused by the provider",
            details={"code": error.get("code"), "request_id": response.get("request_id")},
        )
    if status == "error":
        raise IntegrationUnavailable(
            THE_FORGE,
            error.get("detail") or "provider reported an error",
            details={"code": error.get("code"), "request_id": response.get("request_id")},
        )
    if status not in ("ok", "partial"):
        raise IntegrationRefused(
            THE_FORGE,
            f"unknown response status {status!r}",
            details={"request_id": response.get("request_id")},
        )
    payload = response.get("payload") or {}
    schema = payload.get("schema")
    if schema is not None and schema != EXECUTION_RESULT_SCHEMA:
        raise ContractError(
            f"unexpected execution-result schema {schema!r}",
            details={"expected": EXECUTION_RESULT_SCHEMA},
        )
    return dict(payload)
