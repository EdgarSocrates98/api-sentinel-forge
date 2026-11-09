"""Forge Lens ACL — reference-only trace context (docs/06, Cycle 8).

Forge Lens owns agent/LLM traces; the Sentinel never builds a second
agent APM and never copies trace payloads. The only thing this adapter
returns is `LensTraceRef` — ``{trace_id, session_id, provenance}`` —
so an investigation can *cite* a trace, not ingest it.

Transports:

- ``FileLensACL`` — reads an exported refs manifest
  (`integrations.lens.refs` in workspace config): a JSON file carrying
  ``{"refs": [{trace_id, session_id?, at?, service_id?, provenance?}]}``
  produced by a separately-run `forge-lens` export. Unknown payload keys
  are *dropped*, never copied through — the allowlist is mechanical.
- ``UnavailableLensACL`` — named state when no transport exists.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.integrations.errors import IntegrationUnavailable

LENS_FORGE = "forge-lens"
LENS_MODULE = "forge_lens"

_MAX_REFS = 200


def lens_importable() -> bool:
    """True iff `forge_lens` resolves — never import-for-side-effect."""
    return importlib.util.find_spec(LENS_MODULE) is not None


class LensTraceRef(FrozenModel):
    """A *reference* to a Lens trace — id + provenance, never a payload.

    Constructed only from an allowlisted field set: a ref record with
    trace bodies, prompts or spans cannot smuggle payloads through here —
    there is no field to hold them.
    """

    trace_id: str
    session_id: str | None = None
    service_id: str | None = None
    at: str | None = None
    provenance: str = "unknown"  # observed | imported | derived | unknown


class LensResult(FrozenModel):
    """What a trace-ref lookup produced — refs or a named state."""

    status: str  # available | unavailable | refused
    refs: list[LensTraceRef] = Field(default_factory=list)
    detail: str = ""
    unknowns: list[str] = Field(default_factory=list)


class FileLensACL:
    """Resolve trace refs from a Lens-exported manifest — refs only."""

    def __init__(self, manifest: Path) -> None:
        self._manifest = manifest

    def trace_refs(
        self,
        *,
        service_id: str | None = None,
        incident_id: str | None = None,
    ) -> LensResult:
        try:
            data = json.loads(self._manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return LensResult(
                status="unavailable",
                detail=f"lens refs manifest unreadable: {type(exc).__name__}",
                unknowns=[f"lens_manifest_unreadable:{self._manifest.name}"],
            )
        if not isinstance(data, dict) or not isinstance(
            data.get("refs"), list
        ):
            return LensResult(
                status="refused",
                detail="lens refs manifest must carry a 'refs' list",
                unknowns=["lens_manifest_shape_invalid"],
            )
        refs: list[LensTraceRef] = []
        skipped = 0
        for raw in data["refs"]:
            if not isinstance(raw, dict) or not raw.get("trace_id"):
                skipped += 1
                continue
            # allowlist only — payload keys are dropped by construction
            ref = LensTraceRef(
                trace_id=str(raw["trace_id"]),
                session_id=(
                    str(raw["session_id"]) if raw.get("session_id") else None
                ),
                service_id=(
                    str(raw["service_id"]) if raw.get("service_id") else None
                ),
                at=str(raw["at"]) if raw.get("at") else None,
                provenance=str(raw.get("provenance") or "imported"),
            )
            if service_id and ref.service_id not in (None, service_id):
                continue
            refs.append(ref)
        refs = sorted(refs, key=lambda r: (r.trace_id, r.session_id or ""))
        unknowns = []
        if skipped:
            unknowns.append(f"lens_refs_skipped:{skipped} — entries "
                            "without a trace_id, never name-guessed")
        if len(refs) > _MAX_REFS:
            unknowns.append(f"lens_refs_truncated_at_{_MAX_REFS}")
            refs = refs[:_MAX_REFS]
        return LensResult(
            status="available",
            refs=refs,
            detail=f"{len(refs)} trace ref(s) from {self._manifest.name} — "
            "references only; Forge Lens owns the trace payloads",
            unknowns=unknowns,
        )


class UnavailableLensACL:
    """The named state when no Lens transport exists at all."""

    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason or (
            f"{LENS_MODULE} not importable and no refs manifest configured "
            "— trace context is Lens-owned; an absent Lens is a named "
            "state, never fabricated refs"
        )

    def trace_refs(self, **kwargs: Any) -> LensResult:
        return LensResult(
            status="unavailable",
            detail=self._reason,
            unknowns=["lens_transport_absent"],
        )


def lens_from_config(
    config: dict[str, object], workspace_root: Path | None
) -> Any:
    """Resolve the Lens port: `integrations.lens.refs` manifest or
    unavailable — never a fabricated empty store."""
    integrations = config.get("integrations") if isinstance(config, dict) else None
    lens = (
        integrations.get("lens") if isinstance(integrations, dict) else None
    )
    if not isinstance(lens, dict):
        return UnavailableLensACL(
            "no lens integration configured — set integrations.lens.refs "
            "to a forge-lens exported refs manifest"
        )
    refs = lens.get("refs")
    if not refs:
        return UnavailableLensACL()
    path = Path(str(refs))
    if not path.is_absolute() and workspace_root is not None:
        path = workspace_root / path
    if not path.is_file():
        raise IntegrationUnavailable(
            LENS_FORGE, f"refs manifest not found: {path}"
        )
    return FileLensACL(path)
