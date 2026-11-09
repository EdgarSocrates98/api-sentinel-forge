"""File-backed structural context — reads an exported `atlas-export.json`.

The adapter consumes *only* the versioned `api-sentinel/AtlasExport/v1`
contract — Atlas internals never enter the domain. An absent or malformed
export is a named `IntegrationUnavailable`, never a fabricated empty graph
(degradation contract); an unknown schema version is a `ContractError`.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from api_sentinel.contracts.v1 import AtlasExportV1
from api_sentinel.domain import ApiService, ContractError
from api_sentinel.infrastructure.security.paths import read_text_bounded
from api_sentinel.integrations.atlas.models import StructuralEdge, StructuralRef
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

ATLAS_FORGE = "code-atlas-forge"
ATLAS_REF_KIND = "code_atlas_identity"


class FileStructuralContext:
    """`StructuralContextPort` over a versioned Atlas export file.

    `resolve_service(service)` maps the canonical service to its declared
    `code_atlas_identity` ref (ADR-012 — provenance preserved on the ref);
    `dependencies(atlas_id)`/`dependents(atlas_id)` query the edge index.
    """

    def __init__(self, export: AtlasExportV1) -> None:
        self._export = export
        self._services = {s.atlas_id: s for s in export.services}
        self._edges = [
            StructuralEdge(
                edge_id=e.edge_id,
                from_ref=e.from_ref,
                to_ref=e.to_ref,
                kind=e.kind,
                confidence=e.confidence,
                resolution=e.resolution,
                resolver=e.resolver,
                evidence_refs=list(e.evidence_refs),
            )
            for e in export.edges
        ]

    @classmethod
    def load(cls, path: Path | str) -> FileStructuralContext:
        p = Path(path)
        if not p.is_file():
            raise IntegrationUnavailable(
                ATLAS_FORGE,
                f"no exported Atlas manifest at {p} — export the structural "
                "graph (code-atlas) and point the workspace config at it",
            )
        try:
            data = json.loads(
                read_text_bounded(p, name="atlas export")
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrationUnavailable(
                ATLAS_FORGE, f"Atlas manifest unreadable: {p} ({type(exc).__name__})"
            ) from exc
        try:
            export = AtlasExportV1.model_validate(data)
        except ContractError:
            # An unknown envelope version is a named refusal — never a
            # silent default, never an "unavailable" (the file is there).
            raise
        except PydanticValidationError as exc:
            raise IntegrationRefused(
                ATLAS_FORGE,
                f"Atlas manifest invalid: {p} ({exc.error_count()} errors)",
            ) from exc
        return cls(export)

    @property
    def export(self) -> AtlasExportV1:
        return self._export

    def resolve_service(self, service: ApiService) -> StructuralRef | None:
        ref = next(
            (r for r in service.external_refs if r.kind == ATLAS_REF_KIND),
            None,
        )
        if ref is None:
            return None
        entity = self._services.get(ref.ref)
        if entity is None:
            return None
        return StructuralRef(
            atlas_id=entity.atlas_id,
            name=entity.name,
            kind=entity.kind,
            provenance=ref.provenance,
            evidence_refs=list(entity.evidence_refs),
        )

    def dependencies(self, service_id: str) -> list[StructuralEdge]:
        """Outgoing structural edges from the structural service id."""
        return [e for e in self._edges if e.from_ref == service_id]

    def dependents(self, service_id: str) -> list[StructuralEdge]:
        """Incoming structural edges to the structural service id."""
        return [e for e in self._edges if e.to_ref == service_id]
