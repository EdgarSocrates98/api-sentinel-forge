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
    Capability("api.portal-operations", CapabilityStatus.DECLARED, notes="Cycle 6"),
    Capability("api.aws-readonly-observability", CapabilityStatus.DECLARED, notes="Cycle 7"),
]


def list_capabilities() -> list[Capability]:
    return list(_CAPABILITIES)


def status_report(workspace_initialized: bool, db_present: bool) -> dict[str, object]:
    return {
        "workspace_initialized": workspace_initialized,
        "database": "present" if db_present else "absent",
        "capabilities": {
            "available": sum(1 for c in _CAPABILITIES if c.status is CapabilityStatus.AVAILABLE),
            "declared": sum(1 for c in _CAPABILITIES if c.status is CapabilityStatus.DECLARED),
        },
    }
