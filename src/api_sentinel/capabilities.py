"""Declared capability surface — honest AVAILABLE vs DECLARED states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DECLARED = "declared"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Capability:
    capability_id: str
    status: CapabilityStatus
    since: str | None = None
    notes: str = ""


_AWS_CAPABILITY_ID = "api.aws-readonly-observability"

_CAPABILITIES: list[Capability] = [
    Capability("api.service-inventory", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.telemetry-source-registration", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.evidence-storage", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.persistence-recovery", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.runtime-observation", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.slo-assessment", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.anomaly-detection", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.incident-investigation", CapabilityStatus.AVAILABLE, "0.1.0"),
    Capability("api.deployment-correlation", CapabilityStatus.AVAILABLE, "0.2.5"),
    Capability("api.doctor-handoff", CapabilityStatus.AVAILABLE, "0.2.5"),
    Capability(
        "api.portal-operations",
        CapabilityStatus.AVAILABLE,
        "0.2.5",
        notes="Cycle 6 — local /v1 surface via `api-sentinel serve` (api extra)",
    ),
    Capability(_AWS_CAPABILITY_ID, CapabilityStatus.DECLARED, notes="Cycle 7"),
]


def _aws_capability() -> Capability:
    """AVAILABLE only while the mechanical read-only proof passes.

    `prove_read_only()` exercises `ReadOnlyClient` against a wiretap
    transport: mutating attributes must be refused pre-transport and an
    allowlisted call must go through. If the proof ever fails — e.g. a
    regression widens the allowlist — the capability stays DECLARED and
    says why, rather than advertising an unproven security boundary.
    """
    try:
        from api_sentinel.integrations.aws.client import (
            boto3_available,
            prove_read_only,
        )

        passed, detail = prove_read_only()
        has_boto3 = boto3_available()
    except Exception as exc:  # proof itself broke → honest DECLARED
        return Capability(
            _AWS_CAPABILITY_ID,
            CapabilityStatus.DECLARED,
            notes=f"Cycle 7 — read-only proof could not run: {exc}",
        )
    if not passed:
        return Capability(
            _AWS_CAPABILITY_ID,
            CapabilityStatus.DECLARED,
            notes=f"Cycle 7 — read-only proof failed: {detail}",
        )
    if not has_boto3:
        # the boundary is proven, but real sessions need the optional dep
        return Capability(
            _AWS_CAPABILITY_ID,
            CapabilityStatus.DECLARED,
            notes="Cycle 7 — proof passed; boto3 not installed "
            "(pip install api-sentinel-forge[aws])",
        )
    return Capability(
        _AWS_CAPABILITY_ID,
        CapabilityStatus.AVAILABLE,
        "0.1.0",
        notes=f"Cycle 7 — {detail}",
    )


def list_capabilities() -> list[Capability]:
    return [
        _aws_capability() if c.capability_id == _AWS_CAPABILITY_ID else c
        for c in _CAPABILITIES
    ]


def status_report(workspace_initialized: bool, db_present: bool) -> dict[str, object]:
    capabilities = list_capabilities()
    return {
        "workspace_initialized": workspace_initialized,
        "database": "present" if db_present else "absent",
        "capabilities": {
            "available": sum(
                1 for c in capabilities if c.status is CapabilityStatus.AVAILABLE
            ),
            "declared": sum(
                1 for c in capabilities if c.status is CapabilityStatus.DECLARED
            ),
        },
    }
