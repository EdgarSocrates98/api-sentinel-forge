"""Structural context types — Atlas-owned, never the operational graph.

Two graphs stay conceptually distinct (docs/06): `StructuralEdge` is a
relation found in code/contracts/IaC by Code Atlas; a structural edge never
proves a call happened, and an observed call (`DependencyObservation`,
Sentinel-owned) never proves a code path.
"""

from __future__ import annotations

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel


class StructuralRef(FrozenModel):
    """A resolved structural entity — an Atlas identity with provenance."""

    atlas_id: str
    name: str
    kind: str = "api_service"  # api_service | datastore | queue | external
    provenance: str = "declared"  # provenance of the identity mapping (ADR-012)
    evidence_refs: list[str] = Field(default_factory=list)


class StructuralEdge(FrozenModel):
    """An Atlas-owned relation — declared structure, never an observed call."""

    edge_id: str
    from_ref: str  # atlas_id of the source entity
    to_ref: str  # atlas_id of the target entity
    kind: str  # CALLS_ENDPOINT | REFERENCES | DEPENDS_ON | READS_FROM | …
    confidence: str = "syntactic"  # resolved|syntactic|heuristic|inferred
    resolution: str = "resolved"  # resolved|ambiguous|unresolved|external
    resolver: str = ""  # producing analyzer id/version — provenance
    evidence_refs: list[str] = Field(default_factory=list)
