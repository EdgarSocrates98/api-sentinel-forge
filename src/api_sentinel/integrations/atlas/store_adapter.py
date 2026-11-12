"""Store-backed structural context — reads Code Atlas's real SQLite index.

Code Atlas 1.1.x persists domain intelligence in `atlas.db`
(`domain_entities`, `domain_entity_occurrences`, `domain_relations`,
`evidence`, `snapshots`, `repositories`) — that store IS its real export
surface (no `api-sentinel/AtlasExport/v1` producer exists; C10-F011). This
adapter consumes the store READ-ONLY and maps rows to the ACL's structural
model, preserving provenance verbatim: `snapshot_id`, repository
`identity_key`, `resolver`, `resolution_status`, `confidence_class` and
Atlas `evidence_ids` — never name-only bindings, never promoted evidence.

Config: `integrations.atlas.db_path` (+ optional `repository`,
`snapshot_id`). An absent store → `IntegrationUnavailable`; an ambiguous
repository or a missing READY snapshot → `IntegrationRefused`/named
unknowns — never a fabricated graph.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api_sentinel.domain import ApiService
from api_sentinel.integrations.atlas.models import (
    StructuralEdge,
    StructuralRef,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

ATLAS_FORGE = "code-atlas-forge"
ATLAS_REF_KIND = "code_atlas_identity"

#: Tables the mapping depends on (Atlas schema_version 12 surface).
_REQUIRED_TABLES = (
    "domain_entities",
    "domain_entity_occurrences",
    "domain_relations",
    "evidence",
    "snapshots",
    "repositories",
)

#: domain kinds the Sentinel treats as addressable structural entities.
_KIND_MAP = {
    "api_service": "api_service",
    "api_endpoint": "api_endpoint",
    "datastore": "datastore",
    "queue": "queue",
    "external": "external",
}


@dataclass(frozen=True)
class _EntityRow:
    entity_id: int
    identity_key: str
    qualified_name: str
    name: str
    kind: str


class DbStructuralContext:
    """`StructuralContextPort` over a live Atlas index snapshot.

    Resolution is by exact Atlas identity (`identity_key` or
    `qualified_name` of the service's declared `code_atlas_identity` ref)
    — never a substring/name guess. Every edge keeps Atlas's own
    resolution class (`resolved|external|unresolved|ambiguous`),
    confidence, resolver and evidence ids.
    """

    def __init__(
        self,
        entities: dict[str, _EntityRow],
        edges: list[StructuralEdge],
        *,
        repository_key: str,
        snapshot_id: int,
        stale: bool = False,
    ) -> None:
        self._entities = entities
        self._edges = edges
        self.repository_key = repository_key
        self.snapshot_id = snapshot_id
        self.stale = stale

    # -- loading ------------------------------------------------------

    @classmethod
    def load(
        cls,
        db_path: Path | str,
        *,
        repository: str | None = None,
        snapshot_id: int | None = None,
    ) -> DbStructuralContext:
        p = Path(db_path)
        if not p.is_file():
            raise IntegrationUnavailable(
                ATLAS_FORGE,
                f"no Atlas index at {p} — run `code-atlas index <repo> "
                "--domains api` and point the workspace config at the store",
            )
        try:
            conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise IntegrationUnavailable(
                ATLAS_FORGE, f"Atlas store unreadable: {p} ({exc})"
            ) from exc
        try:
            conn.row_factory = sqlite3.Row
            tables = {
                r[0] for r in conn.execute("select name from sqlite_master where type='table'")
            }
            missing = [t for t in _REQUIRED_TABLES if t not in tables]
            if missing:
                raise IntegrationRefused(
                    ATLAS_FORGE,
                    f"{p} is not an Atlas domain store (missing tables: {', '.join(missing)})",
                )
            repo = cls._resolve_repository(conn, repository)
            snap = cls._resolve_snapshot(conn, repo["id"], snapshot_id)
            return cls._build(conn, repo, snap)
        finally:
            conn.close()

    @classmethod
    def _resolve_repository(cls, conn: sqlite3.Connection, selector: str | None) -> sqlite3.Row:
        repos: list[sqlite3.Row] = conn.execute("select * from repositories").fetchall()
        if selector:
            matched = [
                r
                for r in repos
                if selector in (r["root_handle"], r["identity_key"], r["origin"], str(r["id"]))
            ]
            if not matched:
                raise IntegrationRefused(
                    ATLAS_FORGE,
                    f"no Atlas repository matches {selector!r} ({len(repos)} registered)",
                )
            if len(matched) > 1:
                raise IntegrationRefused(
                    ATLAS_FORGE,
                    f"repository selector {selector!r} is ambiguous "
                    f"({len(matched)} matches) — pick identity_key",
                )
            return matched[0]
        # no selector: a single repository with domain rows is unambiguous
        with_domain = [
            r
            for r in repos
            if conn.execute(
                "select 1 from domain_entities where repository_id=? limit 1",
                (r["id"],),
            ).fetchone()
        ]
        if len(with_domain) == 1:
            return with_domain[0]
        if not with_domain:
            raise IntegrationRefused(
                ATLAS_FORGE,
                "Atlas store has no domain entities — index with "
                "`code-atlas index <repo> --domains api` first",
            )
        raise IntegrationRefused(
            ATLAS_FORGE,
            f"{len(with_domain)} repositories carry domain entities — set "
            "`integrations.atlas.repository` (identity_key) to pick one",
        )

    @classmethod
    def _resolve_snapshot(
        cls,
        conn: sqlite3.Connection,
        repository_id: int,
        snapshot_id: int | None,
    ) -> sqlite3.Row:
        if snapshot_id is not None:
            snap: sqlite3.Row | None = conn.execute(
                "select * from snapshots where id=? and repository_id=?",
                (snapshot_id, repository_id),
            ).fetchone()
            if snap is None:
                raise IntegrationRefused(
                    ATLAS_FORGE,
                    f"snapshot {snapshot_id} not found for repository {repository_id}",
                )
            return snap
        snap = conn.execute(
            "select * from snapshots where repository_id=? and status='READY'"
            " order by id desc limit 1",
            (repository_id,),
        ).fetchone()
        if snap is None:
            raise IntegrationUnavailable(
                ATLAS_FORGE,
                f"repository {repository_id} has no READY snapshot — "
                "the index may be running or failed; honesty beats reuse",
            )
        return snap

    @classmethod
    def _build(
        cls,
        conn: sqlite3.Connection,
        repo: sqlite3.Row,
        snap: sqlite3.Row,
    ) -> DbStructuralContext:
        snap_id = snap["id"]
        entity_rows = conn.execute(
            "select id, identity_key, qualified_name, name, kind "
            "from domain_entities where repository_id=?",
            (repo["id"],),
        ).fetchall()
        occ_entity_ids = {
            r["entity_id"]
            for r in conn.execute(
                "select distinct entity_id from domain_entity_occurrences where snapshot_id=?",
                (snap_id,),
            )
        }
        entities: dict[str, _EntityRow] = {}
        for r in entity_rows:
            if r["id"] not in occ_entity_ids:
                continue  # entity carries no occurrence in this snapshot
            row = _EntityRow(
                entity_id=r["id"],
                identity_key=r["identity_key"],
                qualified_name=r["qualified_name"],
                name=r["name"],
                kind=_KIND_MAP.get(r["kind"], r["kind"]),
            )
            entities[row.identity_key] = row
            entities.setdefault(row.qualified_name, row)

        occ_map = {
            r["id"]: r["entity_id"]
            for r in conn.execute(
                "select id, entity_id from domain_entity_occurrences where snapshot_id=?",
                (snap_id,),
            )
        }
        id_to_entity = {e.entity_id: e for e in entities.values()}
        edges: list[StructuralEdge] = []
        for r in conn.execute(
            "select * from domain_relations where snapshot_id=?",
            (snap_id,),
        ):
            from_ref = (
                cls._ref_name(r["from_entity_occ_id"], occ_map, id_to_entity)
                or f"atlas-symbol:{r['from_symbol_occ_id']}"
            )
            to_ref = cls._ref_name(r["to_entity_occ_id"], occ_map, id_to_entity) or (
                f"unresolved:{r['to_unresolved']}"
                if r["to_unresolved"]
                else f"atlas-symbol:{r['to_symbol_occ_id']}"
            )
            ev = json.loads(r["evidence_ids"] or "[]")
            edges.append(
                StructuralEdge(
                    edge_id=f"atlas-rel:{r['id']}",
                    from_ref=from_ref,
                    to_ref=to_ref,
                    kind=r["type"],
                    confidence=r["confidence_class"] or "syntactic",
                    resolution=r["resolution_status"] or "resolved",
                    resolver=r["resolver"] or "",
                    evidence_refs=[f"atlas-evidence:{e}" for e in ev],
                )
            )
        return cls(
            entities,
            edges,
            repository_key=repo["identity_key"] or str(repo["id"]),
            snapshot_id=snap_id,
            stale=snap["status"] != "READY",
        )

    @staticmethod
    def _ref_name(
        occ_id: Any,
        occ_map: dict[int, int],
        id_to_entity: dict[int, _EntityRow],
    ) -> str | None:
        if occ_id is None:
            return None
        entity = id_to_entity.get(occ_map.get(occ_id, -1))
        return entity.qualified_name if entity else None

    # -- port surface --------------------------------------------------

    def resolve_service(self, service: ApiService) -> StructuralRef | None:
        ref = next(
            (r for r in service.external_refs if r.kind == ATLAS_REF_KIND),
            None,
        )
        if ref is None:
            return None
        entity = self._entities.get(ref.ref)
        if entity is None:
            return None
        return StructuralRef(
            atlas_id=entity.qualified_name,
            name=entity.name,
            kind=entity.kind,
            provenance=ref.provenance,
            evidence_refs=[
                f"atlas-snapshot:{self.snapshot_id}",
                f"atlas-repo:{self.repository_key}",
            ],
        )

    def dependencies(self, service_id: str) -> list[StructuralEdge]:
        return [e for e in self._edges if e.from_ref == service_id]

    def dependents(self, service_id: str) -> list[StructuralEdge]:
        return [e for e in self._edges if e.to_ref == service_id]
