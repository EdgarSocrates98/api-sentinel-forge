"""The structural-context port — what the Sentinel asks of Code Atlas."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from api_sentinel.domain import ApiService
from api_sentinel.integrations.atlas.models import StructuralEdge, StructuralRef


@runtime_checkable
class StructuralContextPort(Protocol):
    """Read-only structural lookups.

    `resolve_service` performs the ADR-012 identity translation: the
    service's declared `code_atlas_identity` external ref maps to an Atlas
    entity — unresolved identities return ``None`` (recorded as an unknown
    by the caller), never a name-matched guess (LAB-006).

    `dependencies` takes the *structural* service id (`StructuralRef.atlas_id`
    — the Atlas-owned identity), and returns outgoing structural edges.
    `dependents` returns the reverse direction.
    """

    def resolve_service(self, service: ApiService) -> StructuralRef | None: ...

    def dependencies(self, service_id: str) -> list[StructuralEdge]: ...

    def dependents(self, service_id: str) -> list[StructuralEdge]: ...
