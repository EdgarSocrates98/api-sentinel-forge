"""API Sentinel → Obsidian reliability review projection.

Composes a sanitized ``theforge/ObsidianNoteRef/v1`` note for SLO/incident
reviews.  Projection only — the vendored ``api_sentinel._forge_obsidian``
engine owns vault bytes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from api_sentinel._forge_obsidian.notes import build_note_ref, section_yaml

FORGE_ID = "api-sentinel-forge"


def build_obsidian_reliability_review(
    *,
    title: str,
    summary: str,
    workspace: str,
    vault_id: str,
    slos: Iterable[Mapping[str, Any]] | None = None,
    incidents: Iterable[Mapping[str, Any]] | None = None,
    evidence: Iterable[Mapping[str, Any]] | None = None,
    related_refs: Iterable[str] | None = None,
    note_path: str | None = None,
    confidence: str = "unknown",
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose a reliability review note (SLOs + incidents + evidence)."""
    return build_note_ref(
        forge_id=FORGE_ID,
        title=title,
        summary=summary,
        workspace=workspace,
        vault_id=vault_id,
        kind="reliability-review",
        body_sections=[
            section_yaml("SLOs", list(slos or [])),
            section_yaml("Incidents", list(incidents or [])),
            section_yaml("Evidence", list(evidence or [])),
        ],
        note_path=note_path,
        confidence=confidence,
        tags=["api", "sentinel", "reliability"],
        related=list(related_refs or []),
        provenance=provenance,
    )


__all__ = ["FORGE_ID", "build_obsidian_reliability_review"]
