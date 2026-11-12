"""Unit tests — DbStructuralContext: real Atlas store consumption (C10-F011).

The fixture is a minimal `atlas.db` built with the same table shapes Code
Atlas 1.1.x writes — domain_entities / occurrences / relations / evidence /
snapshots / repositories — never a hand-authored AtlasExport/v1.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.domain import (
    ApiService,
    ExternalRef,
)
from api_sentinel.integrations.atlas.store_adapter import (
    ATLAS_REF_KIND,
    DbStructuralContext,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)

_SCHEMA = """
create table repositories (id integer primary key, origin text,
    root_handle text, git_provider text, default_branch text,
    created_at text, identity_key text);
create table snapshots (id integer primary key, repository_id integer,
    git_ref text, commit_sha text, worktree_digest text,
    analyzer_versions text, parent_snapshot_id integer, status text,
    error text, created_at text, dirty integer);
create table domain_entities (id integer primary key, repository_id integer,
    domain text, kind text, identity_key text, qualified_name text,
    name text, catalog text, namespace text, environment text,
    attributes text);
create table domain_entity_occurrences (id integer primary key,
    snapshot_id integer, entity_id integer, source_file_id integer,
    span_start integer, span_end integer, line integer, col integer,
    content_hash text, attributes text, extraction_status text);
create table domain_relations (id integer primary key, snapshot_id integer,
    from_entity_occ_id integer, from_symbol_occ_id integer,
    to_entity_occ_id integer, to_symbol_occ_id integer,
    to_unresolved text, type text, resolution_status text,
    confidence_class text, resolver text, evidence_ids text);
create table evidence (id integer primary key, snapshot_id integer,
    source_file_id integer, path text, span text, content_hash text,
    extractor text, extractor_version text, notes text, resolver_id text,
    resolver_version text, evidence_tier text, index_hash text,
    validation_status text);
"""


def _fixture_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute(
        "insert into repositories values (1,'','E:/lab/atlas-repo','','',"
        "'2026-01-01','path:E:/lab/atlas-repo')"
    )
    conn.execute(
        "insert into snapshots values (1,1,'main','abc','dig','{}',null,'READY','','2026-01-01',0)"
    )
    # service entity + two endpoints + one occurrence each
    for eid, kind, ikey, qn, name in (
        (1, "api_service", "dom:api/repo//svc", "svc.app#app", "app"),
        (2, "api_endpoint", "dom:api/repo//get", "GET /orders", "GET /orders"),
        (3, "api_endpoint", "dom:api/repo//post", "POST /orders", "POST /orders"),
    ):
        conn.execute(
            "insert into domain_entities values (?,?,?,?,?,?,?,?,?,?,?)",
            (eid, 1, "api", kind, ikey, qn, name, "", "", "", "{}"),
        )
        conn.execute(
            "insert into domain_entity_occurrences values (?,?,?,?,?,?,?,?,?,?,?)",
            (eid, 1, eid, 1, 0, 10, eid, 1, "h", "{}", "extracted"),
        )
    # CONTAINS edge + an unresolved CALLS_ENDPOINT
    conn.execute(
        "insert into domain_relations values (1,1,1,null,2,null,null,"
        "'CONTAINS','resolved','syntactic','domain:api','[11]')"
    )
    conn.execute(
        "insert into domain_relations values (2,1,null,7,null,null,"
        "'http://orders.local/orders','CALLS_ENDPOINT','external',"
        "'syntactic','domain:api','[12]')"
    )
    conn.execute(
        "insert into domain_relations values (3,1,null,8,null,null,"
        "'/orders','CALLS_ENDPOINT','unresolved','syntactic','domain:api','[13]')"
    )
    conn.commit()
    conn.close()


def _service(ref: str | None) -> ApiService:
    return ApiService(
        service_id="svc_1",
        service_name="orders",
        environment_id="env_1",
        external_refs=(
            [
                ExternalRef(
                    kind=ATLAS_REF_KIND,
                    ref=ref,
                    provenance="verified",
                )
            ]
            if ref
            else []
        ),
        created_at=NOW,
        updated_at=NOW,
    )


class TestLoad:
    def test_absent_store_named_unavailable(self, tmp_path: Path) -> None:
        with pytest.raises(IntegrationUnavailable):
            DbStructuralContext.load(tmp_path / "missing.db")

    def test_not_an_atlas_store_named_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "other.db"
        sqlite3.connect(bad).execute("create table t (x int)")
        with pytest.raises(IntegrationRefused):
            DbStructuralContext.load(bad)

    def test_repository_selector_required_when_ambiguous(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        conn = sqlite3.connect(db)
        conn.execute("insert into repositories values (2,'','E:/other','','','t','path:E:/other')")
        conn.execute(
            "insert into domain_entities values (9,2,'api','api_service',"
            "'k','o#o','o','','','','{}')"
        )
        conn.execute(
            "insert into snapshots values (2,2,'main','def','dig2','{}',null,"
            "'READY','','2026-01-01',0)"
        )
        conn.commit()
        conn.close()
        with pytest.raises(IntegrationRefused, match="pick one"):
            DbStructuralContext.load(db)
        ctx = DbStructuralContext.load(db, repository="path:E:/other")
        assert ctx.repository_key == "path:E:/other"

    def test_unknown_repository_named_refused(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        with pytest.raises(IntegrationRefused, match="no Atlas repository"):
            DbStructuralContext.load(db, repository="path:E:/nope")

    def test_missing_ready_snapshot_named_unavailable(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        conn = sqlite3.connect(db)
        conn.execute("update snapshots set status='STALE'")
        conn.commit()
        conn.close()
        with pytest.raises(IntegrationUnavailable, match="no READY"):
            DbStructuralContext.load(db)

    def test_explicit_snapshot_missing_named_refused(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        with pytest.raises(IntegrationRefused, match="snapshot 99"):
            DbStructuralContext.load(db, snapshot_id=99)


class TestStructuralMapping:
    def test_entities_and_edges_verbatim(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        ctx = DbStructuralContext.load(db)
        assert ctx.snapshot_id == 1
        edges = {e.kind: e for e in ctx._edges}
        assert "CONTAINS" in edges
        calls = [e for e in ctx._edges if e.kind == "CALLS_ENDPOINT"]
        assert {e.resolution for e in calls} == {"external", "unresolved"}
        assert all(e.resolver == "domain:api" for e in calls)
        assert all(e.evidence_refs for e in ctx._edges)

    def test_resolve_service_by_exact_identity(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        ctx = DbStructuralContext.load(db)
        by_key = ctx.resolve_service(_service("dom:api/repo//svc"))
        by_qn = ctx.resolve_service(_service("svc.app#app"))
        assert by_key is not None and by_qn is not None
        assert by_key.atlas_id == by_qn.atlas_id == "svc.app#app"
        assert by_key.provenance == "verified"
        # snapshot + repo provenance travels on the ref
        assert any("atlas-snapshot:1" in r for r in by_key.evidence_refs)

    def test_unresolved_identity_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        ctx = DbStructuralContext.load(db)
        assert ctx.resolve_service(_service("svc.unknown#x")) is None
        assert ctx.resolve_service(_service(None)) is None

    def test_dependencies_direction(self, tmp_path: Path) -> None:
        db = tmp_path / "a.db"
        _fixture_db(db)
        ctx = DbStructuralContext.load(db)
        assert [e.to_ref for e in ctx.dependencies("svc.app#app")] == ["GET /orders"]
        assert ctx.dependents("GET /orders")[0].from_ref == "svc.app#app"
