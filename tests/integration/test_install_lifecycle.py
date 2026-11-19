"""Install lifecycle contract tests (Cycle 16.1 wave 12).

Covers the ``forge/InstallLifecycle/v1`` block derived onto every
receipt and health doc, the incremental-reinstall evidence
(``managed_files`` sha256 + ``created|updated|unchanged|removed``
actions + ``managed_summary``), context-manifest honesty and the
minimal L0 boot surface the install writes into consumer docs.

The bearer-token regression guard lives in ``test_install_2_1.py``
(``test_install_never_emits_token_value`` /
``test_serve_never_emits_token_value``, merged in PR #15).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from api_sentinel.cli import app
from api_sentinel.installation import installations_dir, lifecycle

runner = CliRunner()

FORGE_ID = "api-sentinel-forge"
LIFECYCLE_STATES = {
    "planned",
    "blocked",
    "failed",
    "applied",
    "partially_applied",
    "verified",
    "unverified",
    "ready",
    "certified",
}


@pytest.fixture(autouse=True)
def _forge_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic account registry — never the real ``~/.forge``."""
    monkeypatch.setenv("FORGE_HOME_OVERRIDE", str(tmp_path / ".forge-home"))


def _invoke(*args: str):
    return runner.invoke(app, list(args))


def _receipt(result) -> dict:
    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


def _install(root: Path, *extra: str) -> dict:
    return _receipt(_invoke("install", "--yes", "--json", "--root", str(root), *extra))


# ---------------------------------------------------------------------------
# forge/InstallLifecycle/v1 — derived, closed-vocabulary, additive
# ---------------------------------------------------------------------------


def test_install_receipt_carries_derived_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    payload = _install(root)
    block = payload["lifecycle"]
    assert block["schema"] == "forge/InstallLifecycle/v1"
    assert block["state"] == "verified"  # completed + verification PASS
    assert block["state"] in LIFECYCLE_STATES
    # persisted receipt carries the same derived block as stdout
    persisted = json.loads(
        next((root / ".api-sentinel" / "install" / "receipts").glob("*.json")).read_text()
    )
    assert persisted["lifecycle"] == block


def test_dry_run_lifecycle_is_planned(tmp_path: Path) -> None:
    payload = _receipt(_invoke("install", "--dry-run", "--json", "--root", str(tmp_path / "p")))
    assert payload["lifecycle"]["state"] == "planned"


def test_health_docs_carry_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _install(root)
    status = json.loads(_invoke("install", "status", "--json", "--root", str(root)).stdout)
    assert status["status"] == "healthy"
    assert status["lifecycle"] == {
        "schema": "forge/InstallLifecycle/v1",
        "state": "ready",
        "detail": status["lifecycle"]["detail"],
    }
    doctor = json.loads(_invoke("install", "doctor", "--json", "--root", str(root)).stdout)
    assert doctor["lifecycle"]["schema"] == "forge/InstallLifecycle/v1"
    assert doctor["lifecycle"]["state"] in LIFECYCLE_STATES


def test_uninstalled_health_derives_unverified(tmp_path: Path) -> None:
    status = json.loads(
        _invoke("install", "status", "--json", "--root", str(tmp_path / "absent")).stdout
    )
    assert status["status"] == "unverified"
    assert status["lifecycle"]["state"] == "unverified"


def test_degraded_health_derives_partially_applied(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _install(root)
    (root / ".api-sentinel" / "sentinel.db").write_bytes(b"not a sqlite db")
    status = json.loads(_invoke("install", "status", "--json", "--root", str(root)).stdout)
    assert status["status"] in ("degraded", "broken")
    expected = "partially_applied" if status["status"] == "degraded" else "failed"
    assert status["lifecycle"]["state"] == expected


def test_repair_and_uninstall_receipts_carry_lifecycle(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _install(root)
    repair = _receipt(_invoke("install", "repair", "--yes", "--json", "--root", str(root)))
    assert repair["lifecycle"]["state"] == "verified"
    uninstall = _receipt(_invoke("uninstall", "--yes", "--json", "--root", str(root)))
    assert uninstall["lifecycle"]["state"] == "verified"


def test_repair_dry_run_lifecycle_is_planned(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _install(root)
    doc = json.loads(
        _invoke("install", "repair", "--dry-run", "--json", "--root", str(root)).stdout
    )
    assert doc["status"] == "planned"
    assert doc["lifecycle"]["state"] == "planned"


# ---------------------------------------------------------------------------
# derivation unit table — the state is a view, never a stored verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ({"status": "planned"}, "planned"),
        ({"status": "completed", "verification": {"status": "PASS"}}, "verified"),
        ({"status": "completed", "verification": {"status": "FAIL"}}, "failed"),
        ({"status": "completed", "verification": {"status": "UNVERIFIED"}}, "applied"),
        ({"status": "completed"}, "applied"),
        ({"status": "failed", "checks": [{"status": "FAIL"}]}, "failed"),
        ({"status": "failed", "checks": [{"status": "BLOCKED"}]}, "blocked"),
        ({"status": "blocked"}, "blocked"),
        ({"status": "unverified"}, "unverified"),
        ({}, "unverified"),
        ({"status": "banana"}, "unverified"),
        (
            {
                "status": "completed",
                "verification": {"status": "PASS"},
                "forges": [{"status": "completed"}, {"status": "failed"}],
            },
            "partially_applied",
        ),
    ],
)
def test_receipt_state_derivation(doc: dict, expected: str) -> None:
    state, detail = lifecycle.receipt_state(doc)
    assert state == expected
    assert detail


@pytest.mark.parametrize(
    ("doc", "expected"),
    [
        ({"status": "healthy"}, "ready"),
        ({"status": "degraded"}, "partially_applied"),
        ({"status": "broken"}, "failed"),
        ({"status": "unverified"}, "unverified"),
        ({"status": "banana"}, "unverified"),
    ],
)
def test_health_state_derivation(doc: dict, expected: str) -> None:
    state, _detail = lifecycle.health_state(doc)
    assert state == expected


def test_lifecycle_block_shape() -> None:
    block = lifecycle.receipt_block({"status": "completed", "verification": {"status": "PASS"}})
    assert block["schema"] == lifecycle.LIFECYCLE_SCHEMA == "forge/InstallLifecycle/v1"
    assert set(block) <= {"schema", "state", "detail"}


# ---------------------------------------------------------------------------
# incremental reinstall — §7.4: repeated installs never rewrite
# unchanged assets, and the receipt proves exactly what changed
# ---------------------------------------------------------------------------


def test_incremental_reinstall_reports_unchanged(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    first = _install(root)
    assert first["managed_summary"]["created"] > 0
    assert first["managed_summary"]["updated"] == 0

    def digest(rel: str) -> str:
        return hashlib.sha256((root / rel).read_bytes()).hexdigest()

    state_files = {
        "config.yaml": digest(".api-sentinel/config.yaml"),
        "token": digest(".api-sentinel/token"),
        "sentinel.db": digest(".api-sentinel/sentinel.db"),
    }
    manifest = installations_dir() / f"{FORGE_ID}.json"
    manifest_before = hashlib.sha256(manifest.read_bytes()).hexdigest()

    second = _install(root)
    assert second["status"] == "completed"
    summary = second["managed_summary"]
    # the incremental promise: nothing created, nothing rewritten —
    # the whole managed set is byte-identical.
    assert summary["created"] == 0
    assert summary["updated"] == 0
    assert summary["removed"] == 0
    assert summary["unchanged"] == len(second["managed_files"])
    by_kind = {f["kind"]: f for f in second["managed_files"]}
    for kind in ("config", "db", "token", "account-manifest", "context-manifest"):
        assert by_kind[kind]["action"] == "unchanged", by_kind
    # every entry carries the post-operation sha256 evidence
    for entry in second["managed_files"]:
        assert isinstance(entry["sha256"], str) and len(entry["sha256"]) == 64
    # on-disk proof: the managed bytes really are identical
    for name, before_hash in state_files.items():
        assert digest(f".api-sentinel/{name}") == before_hash
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == manifest_before


def test_reinstall_with_change_reports_updated_only(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _install(root)
    second = _install(root, "--runtime-profile", "aws")
    by_kind = {f["kind"]: f["action"] for f in second["managed_files"]}
    assert by_kind["config"] == "updated"  # deliberate profile convergence
    assert by_kind["db"] == "unchanged"
    assert by_kind["token"] == "unchanged"  # noqa: S105
    assert by_kind["context-manifest"] in ("updated", "unchanged")
    summary = second["managed_summary"]
    assert summary["updated"] >= 1
    assert summary["created"] == 0
    note = second["runtime_profile"]
    assert note["previous_profile"] == "local"
    assert note["effective_profile"] == "aws"


# ---------------------------------------------------------------------------
# context metadata honesty + minimal L0 boot
# ---------------------------------------------------------------------------


def test_context_manifest_metadata_is_honest(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _install(root, "--runtime-profile", "aws", "--profile", "full", "--gateway-profile", "deep")
    cap = root / ".forge" / "context" / "capabilities" / f"{FORGE_ID}.json"
    doc = json.loads(cap.read_text())
    from api_sentinel import _forgecontext as fctx

    assert fctx.validate_manifest(doc) == []
    # metadata reflects the recorded install — never inflated claims
    assert doc["schema"] == "forge/context/v1"
    assert doc["state"] == "available"
    assert doc["installed"]["scope"] == "project"
    assert doc["installed"]["profile"] == "full"  # the tier, not a runtime value
    comp = doc["installed"]["components"]
    assert comp["runtime_profile"] == "aws"
    assert comp["gateway_profile"] == "deep"
    assert comp["installation_kind"] == "service"
    cfg = (root / ".api-sentinel" / "config.yaml").read_text()
    assert "profile: aws" in cfg  # metadata matches the persisted config
    # integrity attestation is real: registry sha256 matches the file
    reg = json.loads((root / ".forge" / "context" / "registry.json").read_text())
    entry = next(f for f in reg["forges"] if f["forge_id"] == FORGE_ID)
    manifest_sha = entry.get("manifest_sha256") or entry.get("sha256")
    if manifest_sha:
        assert manifest_sha == hashlib.sha256(cap.read_bytes()).hexdigest()


def test_l0_boot_surface_is_minimal(tmp_path: Path) -> None:
    """The permanent context an install writes is bounded: the shared
    ecosystem block + one compact per-forge block in AGENTS.md, a thin
    pointer in CLAUDE.md, and the consolidated AGENT_PROTOCOL.md. L0
    stays a boot map — content lives in the capability manifest (L1+)."""
    root = tmp_path / "proj"
    _install(root)
    agents = (root / "AGENTS.md").read_text()
    claude = (root / "CLAUDE.md").read_text()
    protocol = (root / "AGENT_PROTOCOL.md").read_text()
    # managed markers exist and are small (bytes, not guessed tokens)
    assert "<!-- forge:ecosystem:begin -->" in agents
    assert f"<!-- forge:{FORGE_ID}:begin -->" in agents
    assert len(agents) < 8_000
    assert len(claude) < 2_000  # thin @AGENTS.md pointer only
    assert "<!-- forge:agent-protocol:v1 -->" in protocol
    # L0 advertises *where* L1 lives, not L1 content
    assert ".forge/context/registry.json" in agents
    assert f".forge/context/capabilities/{FORGE_ID}.json" in agents


# ---------------------------------------------------------------------------
# host surfaces — AGENTS.md / CLAUDE.md mirror parity (§9.1)
# ---------------------------------------------------------------------------


def test_host_instruction_mirrors_stay_in_sync() -> None:
    """The two host entry files carry the same managed invariants block —
    a drifted mirror silently teaches one host different rules."""
    import re

    repo = Path(__file__).resolve().parents[2]

    def block(name: str) -> str:
        text = (repo / name).read_text(encoding="utf-8")
        m = re.search(
            r"<!-- sentinel:invariants:begin -->.*?<!-- sentinel:invariants:end -->",
            text,
            re.S,
        )
        assert m, f"{name} missing the sentinel:invariants managed block"
        return m.group(0)

    assert block("AGENTS.md") == block("CLAUDE.md")


# ---------------------------------------------------------------------------
# secrets hygiene — wave-12 surface sweep (PR #15 regression kept green)
# ---------------------------------------------------------------------------


def test_lifecycle_surfaces_never_emit_token(tmp_path: Path) -> None:
    """Every emitted surface stays secret-free — the receipt lifecycle
    block, managed digests and health docs carry metadata, never the
    bearer value."""
    root = tmp_path / "proj"
    emitted = _invoke("install", "--yes", "--root", str(root))
    secret = (root / ".api-sentinel" / "token").read_text().strip()
    for out in (
        emitted.stdout + (emitted.stderr or ""),
        _invoke("install", "status", "--root", str(root)).stdout,
        _invoke("install", "doctor", "--root", str(root)).stdout,
    ):
        assert secret not in out
        assert "Bearer" not in out
