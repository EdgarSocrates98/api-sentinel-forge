"""Declared capability surface — honest status + proven stage.

Two axes, never conflated (Cycle-10 §5.4):

- `status` — *current availability*: `available` when the capability can
  serve right now, `declared` when it exists but a precondition is absent
  (optional package, unproven boundary), `unavailable` when it cannot
  run at all. Installed ≠ operational: an optional package alone never
  flips a capability to `available` — the mechanical proof decides.
- `stage` — *highest PROVEN maturity* on the Wave-0 ladder
  `implemented → tested → integrated → verified →
  production_validated`. The data below mirrors the Wave-0 audit
  (`docs/cycles/cycle-10/03-capability-ownership.md`) verbatim — a
  stage is only ever as high as real evidence allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CapabilityStatus(StrEnum):
    AVAILABLE = "available"
    DECLARED = "declared"
    UNAVAILABLE = "unavailable"


class CapabilityStage(StrEnum):
    """Highest proven rung (Wave-0 rubric, docs/cycles/cycle-10/03)."""

    IMPLEMENTED = "implemented"  # code exists at HEAD
    TESTED = "tested"  # automated tests cover it at HEAD
    INTEGRATED = "integrated"  # exercised against the real counterpart
    VERIFIED = "verified"  # proven end-to-end in a real run
    PRODUCTION_VALIDATED = "production_validated"  # homologated externals


@dataclass(frozen=True)
class Capability:
    capability_id: str
    status: CapabilityStatus
    stage: CapabilityStage
    since: str | None = None
    notes: str = ""


_AWS_CAPABILITY_ID = "api.aws-readonly-observability"

# Highest-proven-stage data — copied truthfully from the Wave-0 stage
# matrix (docs/cycles/cycle-10/03-capability-ownership.md); nothing is
# inflated: the four CLI-VERIFIED/forge-TESTED capabilities carry
# `verified` for the capability itself; C10-F010 is closed in Wave 2 —
# every advertised execute pair now has a real executor.
_CAPABILITIES: list[Capability] = [
    Capability(
        "api.service-inventory",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
    ),
    Capability(
        "api.telemetry-source-registration",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
    ),
    Capability(
        "api.evidence-storage",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
    ),
    Capability(
        "api.persistence-recovery",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
    ),
    Capability(
        "api.runtime-observation",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
    ),
    Capability(
        "api.slo-assessment",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
        notes="forge-seam execute pair implemented — Cycle 10 Wave 2",
    ),
    Capability(
        "api.anomaly-detection",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
        notes="forge-seam execute pair implemented — Cycle 10 Wave 2",
    ),
    Capability(
        "api.incident-investigation",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.1.0",
        notes="forge-seam execute pair implemented — Cycle 10 Wave 2",
    ),
    Capability(
        "api.deployment-correlation",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.2.5",
        notes="forge-seam execute pair implemented — Cycle 10 Wave 2",
    ),
    Capability(
        "api.doctor-handoff",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.2.5",
        notes="real in-process Doctor 0.3.1 run completed + routed via "
        "the-forge (run 20261109T131316Z-e7974201) — C10-F009 closed, "
        "Cycle 10 Wave 2",
    ),
    Capability(
        "api.portal-operations",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.TESTED,
        "0.2.5",
        notes="Cycle 6 — local /v1 surface via `api-sentinel serve` (api "
        "extra); no Portal consumes it — C10-F005",
    ),
    Capability(
        _AWS_CAPABILITY_ID,
        CapabilityStatus.DECLARED,
        CapabilityStage.TESTED,
        notes="Cycle 7 — mechanical read-only proof required; no real "
        "AWS account exercised — C10-F008",
    ),
    Capability(
        "api.change-intelligence",
        CapabilityStatus.AVAILABLE,
        CapabilityStage.VERIFIED,
        "0.2.5",
        notes="Cycle 8 — change import/correlation with explicit "
        "proximity≠cause honesty",
    ),
    Capability(
        "api.agentic-investigation",
        CapabilityStatus.DECLARED,
        CapabilityStage.IMPLEMENTED,
        notes="Cycle 8 — bounded context packs + hypothesis lifecycle are "
        "deterministic; agent sessions are Bridge-governed (ADR-010), so "
        "the capability declares until the Bridge seam is live-proven — "
        "C10-F004",
    ),
]


def _aws_capability() -> Capability:
    """AVAILABLE only while the mechanical read-only proof passes.

    `prove_read_only()` exercises `ReadOnlyClient` against a wiretap
    transport: mutating attributes must be refused pre-transport and an
    allowlisted call must go through. If the proof ever fails — e.g. a
    regression widens the allowlist — the capability stays DECLARED and
    says why, rather than advertising an unproven security boundary.
    """
    base = next(
        c for c in _CAPABILITIES if c.capability_id == _AWS_CAPABILITY_ID
    )
    try:
        from api_sentinel.integrations.aws.client import (
            boto3_available,
            prove_read_only,
        )

        passed, detail = prove_read_only()
        has_boto3 = boto3_available()
    except Exception as exc:  # proof itself broke → honest DECLARED
        return Capability(
            base.capability_id,
            CapabilityStatus.DECLARED,
            base.stage,
            notes=f"Cycle 7 — read-only proof could not run: {exc}",
        )
    if not passed:
        return Capability(
            base.capability_id,
            CapabilityStatus.DECLARED,
            base.stage,
            notes=f"Cycle 7 — read-only proof failed: {detail}",
        )
    if not has_boto3:
        # the boundary is proven, but real sessions need the optional dep
        return Capability(
            base.capability_id,
            CapabilityStatus.DECLARED,
            base.stage,
            notes="Cycle 7 — proof passed; boto3 not installed "
            "(pip install api-sentinel-forge[aws])",
        )
    return Capability(
        base.capability_id,
        CapabilityStatus.AVAILABLE,
        base.stage,
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
