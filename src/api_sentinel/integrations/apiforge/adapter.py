"""API Forge ACL — implementation-request handoffs (Cycle 8).

When a hypothesis is VERIFIED the fix itself is API Forge's problem —
the Sentinel never edits code in API Forge's place (docs/03). What it
does is emit a governed `implementation-request` handoff envelope
carrying: the incident, the *verified* hypothesis, evidence refs and a
suggested change — a request, not a patch.

A separately-run API Forge consumes the envelope; its response is
ingested back verbatim as evidence (`ApiForgeIngest` — refs and
summary fields, never reclassified).

Refusal discipline: `implementation_request` refuses non-VERIFIED
hypotheses (`IntegrationRefused`) — delegating a fix for an unproven
cause would fabricate certainty.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.domain.errors import ContractError, ValidationError
from api_sentinel.domain.identity import FrozenModel
from api_sentinel.incidents.models import (
    ConfidenceClass,
    Hypothesis,
    Incident,
)
from api_sentinel.integrations.errors import IntegrationRefused

APIFORGE_FORGE = "api-forge"
APIFORGE_MODULE = "apiforge"
IMPLEMENTATION_REQUEST_KIND = "implementation-request"


def apiforge_importable() -> bool:
    """True iff `apiforge` resolves — never import-for-side-effect."""
    return importlib.util.find_spec(APIFORGE_MODULE) is not None


class ApiForgeMode(StrEnum):
    HANDOFF_FILE = "handoff_file"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ApiForgeResult:
    """What an implementation-request attempt produced — a named state."""

    handoff_id: str
    status: str  # delegated | refused | unavailable | failed
    request_ref: str | None = None
    detail: str = ""


class ApiForgeIngest(FrozenModel):
    """An API Forge response preserved verbatim — evidence, reclassified
    never: findings/unknowns pass through as authored."""

    source_file: str
    handoff_id: str | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    suggested_changes: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class ApiForgeACL:
    """Implementation-request handoffs — file transport or named absence."""

    def __init__(
        self,
        mode: ApiForgeMode | str = ApiForgeMode.HANDOFF_FILE,
        *,
        handoffs_dir: Path | None = None,
    ) -> None:
        self._mode = ApiForgeMode(mode)
        self._dir = handoffs_dir

    @property
    def mode(self) -> ApiForgeMode:
        return self._mode

    def implementation_request(
        self,
        *,
        incident: Incident,
        hypothesis: Hypothesis,
        suggested_change: str,
        evidence_refs: list[str] | None = None,
        at_iso: str = "",
    ) -> ApiForgeResult:
        """Emit a governed `implementation-request` — VERIFIED only.

        A hypothesis that is not VERIFIED is refused before transport:
        API Forge acts on proven causes, not on correlations.
        """
        handoff_id = "hnd_af_" + hashlib.sha256(
            f"{incident.incident_id}|{hypothesis.hypothesis_id}|{at_iso}"
            .encode()
        ).hexdigest()[:24]
        if hypothesis.confidence is not ConfidenceClass.VERIFIED:
            raise IntegrationRefused(
                APIFORGE_FORGE,
                "implementation-request requires a VERIFIED hypothesis — "
                f"{hypothesis.hypothesis_id} is "
                f"{hypothesis.confidence.value}; never delegate an "
                "unproven cause",
            )
        if not suggested_change.strip():
            raise IntegrationRefused(
                APIFORGE_FORGE,
                "implementation-request requires a suggested_change — the "
                "request names the fix, API Forge owns the implementation",
            )
        if self._mode is ApiForgeMode.UNAVAILABLE or self._dir is None:
            return ApiForgeResult(
                handoff_id=handoff_id,
                status="unavailable",
                detail=(
                    f"{APIFORGE_MODULE} not importable and no handoffs "
                    "transport configured — absent integrations never "
                    "simulate success"
                ),
            )
        try:
            envelope = InvestigationHandoffV1(
                schema_=InvestigationHandoffV1.expected_schema,
                handoff_id=handoff_id,
                from_forge="api-sentinel-forge",
                to_forge=APIFORGE_FORGE,
                kind=IMPLEMENTATION_REQUEST_KIND,
                payload={
                    "incident_id": incident.incident_id,
                    "service_id": incident.service_id,
                    "hypothesis_id": hypothesis.hypothesis_id,
                    "hypothesis": hypothesis.text,
                    "verification_id": hypothesis.verification_id or "",
                    "suggested_change": suggested_change,
                    "boundary": (
                        "api-sentinel requests; API Forge owns design and "
                        "implementation — never a code drop"
                    ),
                },
                constraints={
                    "evidence_refs": sorted(
                        set(evidence_refs or []) | set(hypothesis.evidence_refs)
                    ),
                    "scopes": ["propose:implementation"],
                },
            )
            slot = self._dir / handoff_id
            slot.mkdir(parents=True, exist_ok=True)
            path = slot / "implementation_request.json"
            path.write_text(
                envelope.model_dump_json(indent=2, by_alias=True),
                encoding="utf-8",
            )
            return ApiForgeResult(
                handoff_id=handoff_id,
                status="delegated",
                request_ref=str(path),
                detail=(
                    "implementation-request emitted; a separately-run API "
                    "Forge consumes the envelope and returns a response "
                    "via ingest"
                ),
            )
        except OSError as exc:
            return ApiForgeResult(
                handoff_id=handoff_id,
                status="failed",
                detail=f"handoff could not be emitted: {exc}",
            )

    def ingest(self, response_path: Path) -> ApiForgeIngest:
        """Read an API Forge response back as verbatim evidence."""
        if not response_path.is_file():
            raise ValidationError(
                f"api-forge response not found: {response_path}"
            )
        try:
            data = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                f"api-forge response unreadable: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ContractError("api-forge response must be a JSON object")
        try:
            return ApiForgeIngest(
                source_file=str(response_path),
                handoff_id=data.get("handoff_id"),
                findings=[dict(f) for f in data.get("findings", [])],
                suggested_changes=[
                    str(s) for s in data.get("suggested_changes", [])
                ],
                unknowns=[str(u) for u in data.get("unknowns", [])],
            )
        except (TypeError, PydanticValidationError, ValueError) as exc:
            raise ContractError(
                f"api-forge response shape invalid: {exc}"
            ) from exc


def apiforge_from_config(
    config: dict[str, object], workspace_root: Path | None
) -> ApiForgeACL:
    """Resolve the adapter: `integrations.apiforge.mode` —
    `handoff_file` (default) or `unavailable`."""
    integrations = config.get("integrations") if isinstance(config, dict) else None
    af = (
        integrations.get("apiforge")
        if isinstance(integrations, dict)
        else None
    )
    mode = (
        str(af.get("mode", "handoff_file"))
        if isinstance(af, dict)
        else "handoff_file"
    )
    handoffs_dir = (
        (workspace_root / "handoffs") if workspace_root is not None else None
    )
    if handoffs_dir is None and mode != "unavailable":
        mode = "unavailable"
    return ApiForgeACL(mode=ApiForgeMode(mode), handoffs_dir=handoffs_dir)
