"""Sentinel ↔ Doctor translation — the anti-corruption layer.

Outbound: `api-sentinel/InvestigationHandoff/v1` → Doctor's public
`ForgeRequest` (protocol v2) dict — the only shape DoctorBoundary accepts.

Inbound: a Doctor `ApiHandoffBundle`-shaped dict → Sentinel-side
`DoctorIngest`, carrying findings and unknowns *verbatim* (never
reclassified) with their provenance. The Sentinel stores Doctor output as
evidence; it does not adopt Doctor conclusions as its own.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError

from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.domain import ContractError
from api_sentinel.domain.identity import FrozenModel

DOCTOR_FORGE = "forge-doctor-api"
DOCTOR_PROTOCOL_VERSION = 2
_DOCTOR_SUPPORTED_PROTOCOLS = (1, 2)

#: Sentinel handoff kind routed to the Doctor ACL.
DOCTOR_INVESTIGATION_KIND = "api-investigation"


class ForgeRequestV2(BaseModel):
    """The Doctor `ForgeRequest` public shape (protocol v2 emission).

    Mirrors `forge_doctor_api.handoff.protocol.ForgeRequest` — a dict twin
    used on the wire so the Sentinel never imports Doctor internals.
    """

    model_config = ConfigDict(extra="forbid")
    protocol_version: int = DOCTOR_PROTOCOL_VERSION
    request_id: str
    target: str
    capabilities: list[str] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)
    delta: dict[str, Any] | None = None
    clock: str | None = None


class DoctorIngest(FrozenModel):
    """Doctor output preserved verbatim — findings/unknowns never reclassified."""

    handoff_id: str | None = None
    analysis_rev: str | None = None
    service: str | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    unknowns: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    context_refs: list[str] = Field(default_factory=list)


def handoff_to_doctor_request(
    handoff: InvestigationHandoffV1,
    *,
    target: str,
    request_id: str | None = None,
    capabilities: list[str] | None = None,
    clock: str | None = None,
) -> dict[str, Any]:
    """Translate a v1 handoff envelope into Doctor's `ForgeRequest` dict.

    Version-strict: only `api-sentinel/InvestigationHandoff/v1` is accepted —
    any other schema raises `ContractError` (never a silent default).

    `target` is Doctor's scan root (a project path or context ref the
    caller resolved); `capabilities` are the Sentinel capability ids being
    requested (e.g. `api.diagnose`); `context_refs` point at evidence the
    Doctor can resolve.
    """
    if handoff.schema_ != InvestigationHandoffV1.expected_schema:
        raise ContractError(
            f"unsupported handoff schema '{handoff.schema_}'",
            details={
                "received": handoff.schema_,
                "expected": InvestigationHandoffV1.expected_schema,
            },
        )
    payload_caps = capabilities if capabilities is not None else list(
        handoff.payload.get("capabilities", [])
    )
    refs = list(handoff.constraints.evidence_refs)
    for ref in handoff.payload.get("context_refs", []):
        if ref not in refs:
            refs.append(ref)
    request = ForgeRequestV2(
        protocol_version=DOCTOR_PROTOCOL_VERSION,
        request_id=request_id or handoff.handoff_id,
        target=target,
        capabilities=sorted(payload_caps),
        context_refs=sorted(refs),
        delta=handoff.payload.get("delta"),
        clock=clock,
    )
    return request.model_dump(mode="json", exclude_none=True)


def ingest_doctor_bundle(data: dict[str, Any]) -> DoctorIngest:
    """Parse a Doctor-emitted `ApiHandoffBundle`-shaped dict.

    Findings and unknowns are preserved verbatim — the Sentinel records
    them as evidence, never reclassified. A malformed payload raises
    `ContractError`; an unknown `handoff_version` is rejected explicitly.
    """
    if not isinstance(data, dict):
        raise ContractError("doctor bundle is not a JSON object")
    version = data.get("handoff_version", 1)
    if version not in (1, 2):
        raise ContractError(
            f"unsupported doctor handoff_version {version!r}",
            details={"supported": [1, 2]},
        )
    try:
        return DoctorIngest(
            handoff_id=data.get("handoff_id") or None,
            analysis_rev=data.get("analysis_rev"),
            service=data.get("service"),
            findings=[dict(f) for f in data.get("findings", [])],
            unknowns=[dict(u) for u in data.get("unknowns", [])],
            capabilities=[dict(c) for c in data.get("capabilities", [])],
            context_refs=list(data.get("context_refs", [])),
        )
    except (TypeError, AttributeError, PydanticValidationError) as exc:
        raise ContractError(f"doctor bundle malformed: {exc}") from exc


def ingest_doctor_bundle_json(text: str) -> DoctorIngest:
    """Parse a Doctor bundle from raw JSON text."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"doctor bundle is not valid JSON: {exc}") from exc
    return ingest_doctor_bundle(data)
