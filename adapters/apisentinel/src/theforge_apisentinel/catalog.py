"""What the adapter exposes of API Sentinel: the public seam map + surface snapshot.

``native_surface.json`` records the Sentinel's *public boundary surface*,
rewritten by ``python -m theforge_apisentinel.record`` in the specialist's
interpreter. A capability is exposed only when the snapshot says its seam is
present; a drifted specialist degrades to manifest limitations, never a
runtime surprise.

Capability ids are declared against the *live catalog*: ``record.py``
drives the real `SentinelBoundary.describe()`/`capabilities()` and records
which of the five ids came back supported — the verification evidence the
Cycle-5 acceptance criterion requires (semantic collisions surface here,
never worked around).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SNAPSHOT_PATH = Path(__file__).with_name("native_surface.json")
VERIFICATION_PATH = Path(__file__).with_name("catalog_verification.json")
DOMAINS = ("api",)
HAND_BUILT = "hand-built"

#: The five capability ids Cycle 5 declares — verified live, never assumed.
EXPECTED_CAPABILITIES: tuple[str, ...] = (
    "api.runtime-observation",
    "api.slo-assessment",
    "api.anomaly-detection",
    "api.incident-investigation",
    "api.deployment-correlation",
)

SEAM_BOUNDARY = "sentinel_boundary"
SEAM_HANDOFF = "handoff_translate"


@dataclass(frozen=True)
class CapabilitySpec:
    """How a capability runs: its seam, actions and routing signals."""

    seam: str
    actions: tuple[str, ...]
    description: str
    finding_class: str = "runtime"
    signals_keywords: tuple[str, ...] = ()
    relations: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


CAPABILITY_MAP: Mapping[str, CapabilitySpec] = {
    "api.runtime-observation": CapabilitySpec(
        seam=SEAM_BOUNDARY,
        actions=("summarize",),
        description="Temporal observation of a running API: windowed "
        "metrics, evidence refs and quality state the Sentinel persisted.",
        signals_keywords=("api observation", "runtime telemetry", "api latency"),
    ),
    "api.slo-assessment": CapabilitySpec(
        seam=SEAM_BOUNDARY,
        actions=("evaluate",),
        description="Windowed SLO evaluation: SLI, error budget and breach "
        "status against a versioned objective.",
        signals_keywords=("slo", "sli", "error budget", "availability target"),
    ),
    "api.anomaly-detection": CapabilitySpec(
        seam=SEAM_BOUNDARY,
        actions=("run",),
        description="Deterministic anomaly detection over registered rules: "
        "deviations with evidence refs and explicit unknowns.",
        signals_keywords=("anomaly", "detection", "degradation", "deviation"),
    ),
    "api.incident-investigation": CapabilitySpec(
        seam=SEAM_BOUNDARY,
        actions=("list", "show"),
        description="Incident lifecycle and investigation journal: open "
        "incidents, hypotheses and change correlation.",
        signals_keywords=("incident", "investigate", "triage", "postmortem"),
    ),
    "api.deployment-correlation": CapabilitySpec(
        seam=SEAM_BOUNDARY,
        actions=("correlate",),
        description="Change/dependency correlation: observed edges, "
        "structural context and honest unknowns — never causal certainty.",
        signals_keywords=("correlate", "deployment", "change", "cascade"),
    ),
}


class SnapshotError(ValueError):
    """``native_surface.json`` is missing or malformed."""


def _seam(data: object, index: int) -> dict[str, Any]:
    where = f"snapshot.seams[{index}]"
    if not isinstance(data, dict):
        raise SnapshotError(f"{where}: expected an object")
    for key in ("name", "module", "callable"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise SnapshotError(f"{where}.{key}: expected a non-empty string")
    if type(data.get("present")) is not bool:
        raise SnapshotError(f"{where}.present: expected a boolean")
    return dict(data)


def validate_snapshot(data: object) -> dict[str, Any]:
    """The snapshot as a dict, or ``SnapshotError`` naming the bad field."""
    if not isinstance(data, dict):
        raise SnapshotError("snapshot: expected an object")
    for key in ("specialist_version", "recorded_at", "provenance", "protocol_version"):
        if not isinstance(data.get(key), (str, int)) or data.get(key) in (None, ""):
            raise SnapshotError(
                f"snapshot.{key}: expected a non-empty string or integer"
            )
    seams = data.get("seams")
    if not isinstance(seams, list):
        raise SnapshotError("snapshot.seams: expected a list")
    seen: set[str] = set()
    for index, item in enumerate(seams):
        seam = _seam(item, index)
        if seam["name"] in seen:
            raise SnapshotError(
                f"snapshot.seams[{index}]: duplicate seam {seam['name']!r}"
            )
        seen.add(seam["name"])
    return data


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, Any]:
    """Read and validate the packaged surface snapshot."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SnapshotError(f"{path.name}: unreadable ({type(exc).__name__})") from None
    return validate_snapshot(data)


def seam_present(snapshot: Mapping[str, Any], name: str) -> bool:
    """Whether the recorded surface has the seam an exposed capability needs."""
    return any(
        isinstance(item, Mapping)
        and item.get("name") == name
        and item.get("present") is True
        for item in snapshot.get("seams") or ()
    )


def native_fingerprint(snapshot: Mapping[str, Any]) -> str:
    """The sha256 the manifest declares as ``native_surface_fingerprint``."""
    payload = {k: v for k, v in snapshot.items() if k != "recorded_at"}
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def capability_entry(spec: CapabilitySpec) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "actions": list(spec.actions),
        "default_action": spec.actions[0],
        "state": "supported",
        "operation_class": "read_only",
        "description": spec.description,
        "finding_class": spec.finding_class,
        "signals": {
            "keywords": list(spec.signals_keywords),
            "file_globs": [],
            "dependencies": [],
        },
    }
    if spec.relations:
        entry["relations"] = {k: list(v) for k, v in spec.relations.items()}
    return entry


def manifest_payload(
    snapshot: Mapping[str, Any],
    *,
    provider_id: str,
    version: str,
    ops: Sequence[str] = ("describe", "health", "capabilities", "execute"),
) -> dict[str, Any]:
    """The ``ForgeManifest`` v1 payload derived from a validated snapshot."""
    capabilities: list[dict[str, Any]] = []
    limitations: list[str] = []
    if snapshot.get("provenance") == HAND_BUILT:
        limitations.append(
            "native surface snapshot is hand-built (provisional until "
            "re-recorded with python -m theforge_apisentinel.record)"
        )
    for capability_id in sorted(CAPABILITY_MAP):
        spec = CAPABILITY_MAP[capability_id]
        if seam_present(snapshot, spec.seam):
            capabilities.append({"id": capability_id, **capability_entry(spec)})
        else:
            limitations.append(
                f"capability '{capability_id}' not exposed: seam "
                f"{spec.seam!r} absent from the recorded native surface"
            )
    return {
        "schema": "theforge/ForgeManifest/v1",
        "id": provider_id,
        "version": version,
        "protocols": ["forge/v1"],
        "ops": list(ops),
        "domains": list(DOMAINS),
        "capabilities": capabilities,
        "execution": {"local": True, "offline": True, "requires_network": False},
        "limitations": limitations,
        "unknowns": [],
        "context_revalidation": "hash",
        "features": ["handoff/v1"],
        "adapter_version": version,
        "native_surface_fingerprint": native_fingerprint(snapshot),
    }
