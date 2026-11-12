"""Unit tests — Atlas file adapter: two graphs distinct, named UNAVAILABLE."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.domain import ApiService, ContractError, ExternalRef
from api_sentinel.integrations.atlas.file_adapter import (
    ATLAS_REF_KIND,
    FileStructuralContext,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "lab/fixtures/lab-003-dependency-cascade/atlas-export.json"
)


def _svc(**over) -> ApiService:
    base = {
        "service_id": "svc_orders",
        "service_name": "orders",
        "environment_id": "env_lab",
        "external_refs": [
            ExternalRef(
                kind=ATLAS_REF_KIND,
                ref="atlas:svc:orders",
                provenance="verified",
            )
        ],
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(over)
    return ApiService(**base)


class TestAtlasLoad:
    def test_absent_manifest_named_unavailable(self, tmp_path: Path) -> None:
        with pytest.raises(IntegrationUnavailable) as exc:
            FileStructuralContext.load(tmp_path / "nonexistent.json")
        assert exc.value.forge == "code-atlas-forge"

    def test_malformed_manifest_named_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        # Unreadable file — can't establish the transport → unavailable.
        with pytest.raises(IntegrationUnavailable):
            FileStructuralContext.load(bad)

    def test_invalid_manifest_named_refused(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "schema": "api-sentinel/AtlasExport/v1",
                    "exported_at": "2026-01-01T00:00:00Z",
                    "services": [{"name": "orders"}],  # missing required atlas_id
                }
            ),
            encoding="utf-8",
        )
        # Well-formed JSON, wrong shape → named refusal, never a crash.
        with pytest.raises(IntegrationRefused):
            FileStructuralContext.load(bad)

    def test_unknown_schema_version_rejected_explicitly(self, tmp_path: Path) -> None:
        bad = tmp_path / "v9.json"
        bad.write_text(
            json.dumps(
                {
                    "schema": "api-sentinel/AtlasExport/v9",
                    "exported_at": "2026-01-01T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        # Unknown version is rejected explicitly (ADR-006) — ContractError.
        with pytest.raises(ContractError):
            FileStructuralContext.load(bad)

    def test_valid_manifest_loads(self) -> None:
        ctx = FileStructuralContext.load(FIXTURE)
        assert ctx.export.exporter.id == "code-atlas-forge"
        assert len(ctx.export.services) == 4
        assert len(ctx.export.edges) == 3


class TestResolveService:
    def test_resolves_declared_atlas_identity(self) -> None:
        ctx = FileStructuralContext.load(FIXTURE)
        ref = ctx.resolve_service(_svc())
        assert ref is not None
        assert ref.atlas_id == "atlas:svc:orders"
        assert ref.name == "orders"
        assert ref.provenance == "verified"  # ADR-012 provenance carried through

    def test_unresolved_identity_returns_none_never_guessed(self) -> None:
        ctx = FileStructuralContext.load(FIXTURE)
        # A service with no code_atlas_identity — LAB-006: stays unresolved.
        no_ref = _svc(external_refs=[])
        assert ctx.resolve_service(no_ref) is None
        # A ref pointing at a non-existent atlas entity — no name-match guess.
        dangling = _svc(
            external_refs=[ExternalRef(kind=ATLAS_REF_KIND, ref="atlas:svc:nonexistent")]
        )
        assert ctx.resolve_service(dangling) is None


class TestStructuralEdges:
    def test_dependencies_are_structural_never_observed(self) -> None:
        ctx = FileStructuralContext.load(FIXTURE)
        edges = ctx.dependencies("atlas:svc:orders")
        assert len(edges) == 1
        e = edges[0]
        # Labeled structural — a code/contract relation, never an observed call.
        assert e.kind == "CALLS_ENDPOINT"
        assert e.to_ref == "atlas:svc:payments"
        assert e.resolver == "openapi-imports/1.4"  # provenance preserved

    def test_dependents_reverse_direction(self) -> None:
        ctx = FileStructuralContext.load(FIXTURE)
        deps = ctx.dependents("atlas:svc:payments")
        assert len(deps) == 1
        assert deps[0].from_ref == "atlas:svc:orders"
