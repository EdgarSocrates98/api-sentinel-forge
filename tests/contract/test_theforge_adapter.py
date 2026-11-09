"""Contract tests for the shipped-for-review the-forge adapter scaffold.

The adapter lives under ``adapters/apisentinel/`` — a separate stdlib-only
distribution copied into ``the-forge/adapters/apisentinel`` at registration
(never modifying the-forge). These tests validate the catalog snapshot, the
capability-map ↔ live-catalog agreement, and that the five declared ids are
the ones the Sentinel boundary reports.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ADAPTER_SRC = (
    Path(__file__).resolve().parents[2] / "adapters" / "apisentinel" / "src"
)

pytestmark = pytest.mark.skipif(
    not (ADAPTER_SRC / "theforge_apisentinel").is_dir(),
    reason="adapter scaffold not present",
)

if str(ADAPTER_SRC) not in sys.path:
    sys.path.insert(0, str(ADAPTER_SRC))


def _import_catalog():
    from theforge_apisentinel import catalog

    return catalog


def test_native_surface_snapshot_valid_and_fingerprinted():
    catalog = _import_catalog()
    snap = catalog.load_snapshot()
    assert snap["provenance"] in {"recorded", "hand-built"}
    assert any(s["name"] == "sentinel_boundary" and s["present"] for s in snap["seams"])
    fp = catalog.native_fingerprint(snap)
    assert len(fp) == 64


def test_capability_map_covers_the_five_declared_ids():
    catalog = _import_catalog()
    assert set(catalog.CAPABILITY_MAP) == set(catalog.EXPECTED_CAPABILITIES)
    assert set(catalog.EXPECTED_CAPABILITIES) == {
        "api.runtime-observation",
        "api.slo-assessment",
        "api.anomaly-detection",
        "api.incident-investigation",
        "api.deployment-correlation",
    }


def test_manifest_exposes_supported_capabilities_from_snapshot():
    catalog = _import_catalog()
    snap = catalog.load_snapshot()
    manifest = catalog.manifest_payload(snap, provider_id="api-sentinel-forge", version="0.1.0")
    assert manifest["schema"] == "theforge/ForgeManifest/v1"
    ids = {c["id"] for c in manifest["capabilities"]}
    assert ids == set(catalog.EXPECTED_CAPABILITIES)
    for cap in manifest["capabilities"]:
        assert cap["state"] == "supported"
        assert cap["finding_class"] == "runtime"


def test_catalog_verification_records_live_support(tmp_path):
    """The recorded verification asserts the five ids came back supported."""
    verification_path = (
        ADAPTER_SRC / "theforge_apisentinel" / "catalog_verification.json"
    )
    if not verification_path.exists():
        pytest.skip("catalog_verification.json not yet recorded")
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification["all_supported"] is True
    assert verification["collisions"] == []
    assert set(verification["expected"]) == {
        "api.runtime-observation",
        "api.slo-assessment",
        "api.anomaly-detection",
        "api.incident-investigation",
        "api.deployment-correlation",
    }
    assert all(v == "supported" for v in verification["live_states"].values())


def test_adapter_never_imports_theforge():
    """The adapter scaffold may not import ``theforge`` (the control plane)."""
    import re

    pattern = re.compile(r"(?:import|from)\s+theforge(?![\w])")
    for py in (ADAPTER_SRC / "theforge_apisentinel").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        # `theforge_apisentinel` (the adapter's own package) must not match;
        # `theforge`/`theforge.x` (the control plane) must.
        assert not pattern.search(text), f"{py.name} imports theforge"
