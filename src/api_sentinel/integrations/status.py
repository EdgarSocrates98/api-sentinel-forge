"""Integration status — every sibling Forge gets a named honest state.

`AVAILABLE` = a real transport usable now (importable module / readable
manifest / local boundary). `UNVERIFIED` = a transport exists but the far
side was never live-confirmed (handoff_file emits envelopes a Doctor may
or may not consume). `UNAVAILABLE` = nothing can carry a delegation —
the named refusal state, never a fabricated success (docs/06).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from api_sentinel.domain import SentinelError
from api_sentinel.integrations.atlas.file_adapter import (
    ATLAS_FORGE,
    FileStructuralContext,
)
from api_sentinel.integrations.doctor.adapter import (
    DoctorMode,
    doctor_importable,
)
from api_sentinel.integrations.doctor.translate import DOCTOR_FORGE
from api_sentinel.integrations.the_forge.handoff import THE_FORGE


class IntegrationState(StrEnum):
    AVAILABLE = "available"
    UNVERIFIED = "unverified"
    UNAVAILABLE = "unavailable"


def _doctor_state() -> dict[str, object]:
    if doctor_importable():
        return {
            "forge": DOCTOR_FORGE,
            "state": IntegrationState.AVAILABLE.value,
            "mode": DoctorMode.IN_PROCESS.value,
            "reason": "forge_doctor_api importable — in-process delegation "
                      "through DoctorBoundary",
        }
    return {
        "forge": DOCTOR_FORGE,
        "state": IntegrationState.UNVERIFIED.value,
        "mode": DoctorMode.HANDOFF_FILE.value,
        "reason": "forge_doctor_api not importable — handoff_file transport "
                  "emits envelopes; the Doctor peer is unconfirmed",
    }


def _atlas_state(atlas_manifest: Path | None) -> dict[str, object]:
    if atlas_manifest is None:
        return {
            "forge": ATLAS_FORGE,
            "state": IntegrationState.UNAVAILABLE.value,
            "mode": "file",
            "reason": "no atlas export configured — set "
                      "integrations.atlas.export in workspace config",
        }
    try:
        FileStructuralContext.load(atlas_manifest)
    except SentinelError as exc:
        # Absent/unreadable → unavailable; unknown version/refused → still a
        # named unavailability (the transport is not usable), reason preserved.
        reason = getattr(exc, "reason", exc.message)
        return {
            "forge": ATLAS_FORGE,
            "state": IntegrationState.UNAVAILABLE.value,
            "mode": "file",
            "reason": reason,
        }
    return {
        "forge": ATLAS_FORGE,
        "state": IntegrationState.AVAILABLE.value,
        "mode": "file",
        "reason": f"exported manifest readable at {atlas_manifest}",
    }


def _the_forge_state() -> dict[str, object]:
    return {
        "forge": THE_FORGE,
        "state": IntegrationState.AVAILABLE.value,
        "mode": "local_boundary",
        "reason": "the Sentinel's forge/v1 provider seam lives in this "
                  "process — describe/health/capabilities/execute are local",
    }


def integration_status(atlas_manifest: Path | None = None) -> list[dict[str, object]]:
    """Report every sibling Forge's honest state, deterministically sorted."""
    return sorted(
        (
            _atlas_state(atlas_manifest),
            _doctor_state(),
            _the_forge_state(),
        ),
        key=lambda s: str(s["forge"]),
    )
