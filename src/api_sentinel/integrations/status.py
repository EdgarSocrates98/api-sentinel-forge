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
from api_sentinel.integrations.apiforge.adapter import (
    APIFORGE_FORGE,
    apiforge_importable,
)
from api_sentinel.integrations.atlas.file_adapter import (
    ATLAS_FORGE,
    FileStructuralContext,
)
from api_sentinel.integrations.bridge.adapter import (
    BRIDGE_FORGE,
    bridge_importable,
)
from api_sentinel.integrations.data_sentinel.adapter import (
    DATA_SENTINEL_FORGE,
    data_sentinel_importable,
)
from api_sentinel.integrations.doctor.adapter import (
    DoctorMode,
    doctor_importable,
)
from api_sentinel.integrations.doctor.translate import DOCTOR_FORGE
from api_sentinel.integrations.lens.adapter import (
    LENS_FORGE,
    lens_importable,
)
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


def _atlas_state(
    atlas_manifest: Path | None,
    atlas_db: Path | None = None,
    atlas_repository: str | None = None,
    atlas_snapshot_id: int | None = None,
) -> dict[str, object]:
    if atlas_db is not None:
        try:
            from api_sentinel.integrations.atlas.store_adapter import (
                DbStructuralContext,
            )

            DbStructuralContext.load(
                atlas_db,
                repository=atlas_repository,
                snapshot_id=atlas_snapshot_id,
            )
        except SentinelError as exc:
            reason = getattr(exc, "reason", exc.message)
            return {
                "forge": ATLAS_FORGE,
                "state": IntegrationState.UNAVAILABLE.value,
                "mode": "db",
                "reason": reason,
            }
        return {
            "forge": ATLAS_FORGE,
            "state": IntegrationState.AVAILABLE.value,
            "mode": "db",
            "reason": f"real Atlas index store readable at {atlas_db}",
        }
    if atlas_manifest is None:
        return {
            "forge": ATLAS_FORGE,
            "state": IntegrationState.UNAVAILABLE.value,
            "mode": "file",
            "reason": "no atlas export configured — set "
                      "integrations.atlas.db_path or .export in "
                      "workspace config",
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


def _bridge_state() -> dict[str, object]:
    if bridge_importable():
        return {
            "forge": BRIDGE_FORGE,
            "state": IntegrationState.UNVERIFIED.value,
            "mode": "in_process",
            "reason": "forge_agent_bridge importable — session governance "
                      "is Bridge-owned; live delegation not yet confirmed",
        }
    return {
        "forge": BRIDGE_FORGE,
        "state": IntegrationState.UNVERIFIED.value,
        "mode": "file",
        "reason": "forge_agent_bridge not importable — governed session "
                  "requests emit envelopes under agent_sessions/ for a "
                  "separately-run Bridge; the peer is unconfirmed",
    }


def _lens_state() -> dict[str, object]:
    if lens_importable():
        return {
            "forge": LENS_FORGE,
            "state": IntegrationState.UNVERIFIED.value,
            "mode": "in_process",
            "reason": "forge_lens importable — trace references resolve "
                      "through the Lens store; payloads stay Lens-owned",
        }
    return {
        "forge": LENS_FORGE,
        "state": IntegrationState.UNAVAILABLE.value,
        "mode": "file",
        "reason": "forge_lens not importable — set integrations.lens.refs "
                  "to an exported refs manifest for trace references",
    }


def _data_sentinel_state() -> dict[str, object]:
    if data_sentinel_importable():
        return {
            "forge": DATA_SENTINEL_FORGE,
            "state": IntegrationState.UNVERIFIED.value,
            "mode": "in_process",
            "reason": "data_sentinel importable — domain-triggered "
                      "stale-data handoffs only",
        }
    return {
        "forge": DATA_SENTINEL_FORGE,
        "state": IntegrationState.UNVERIFIED.value,
        "mode": "handoff_file",
        "reason": "data_sentinel not importable — stale-data handoffs "
                  "emit envelopes under handoffs/ for a separately-run "
                  "peer; generic telemetry never leaves",
    }


def _apiforge_state() -> dict[str, object]:
    if apiforge_importable():
        return {
            "forge": APIFORGE_FORGE,
            "state": IntegrationState.UNVERIFIED.value,
            "mode": "in_process",
            "reason": "apiforge importable — implementation-request "
                      "handoffs require VERIFIED hypotheses",
        }
    return {
        "forge": APIFORGE_FORGE,
        "state": IntegrationState.UNVERIFIED.value,
        "mode": "handoff_file",
        "reason": "apiforge not importable — implementation-request "
                  "envelopes emit under handoffs/ for a separately-run "
                  "peer; the Sentinel never edits API Forge's code",
    }


def integration_status(
    atlas_manifest: Path | None = None,
    atlas_db: Path | None = None,
    atlas_repository: str | None = None,
    atlas_snapshot_id: int | None = None,
) -> list[dict[str, object]]:
    """Report every sibling Forge's honest state, deterministically sorted."""
    return sorted(
        (
            _apiforge_state(),
            _atlas_state(
                atlas_manifest, atlas_db, atlas_repository, atlas_snapshot_id
            ),
            _bridge_state(),
            _data_sentinel_state(),
            _doctor_state(),
            _lens_state(),
            _the_forge_state(),
        ),
        key=lambda s: str(s["forge"]),
    )
