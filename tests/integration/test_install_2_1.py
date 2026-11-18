"""Install-2.1 contract tests (Cycle 16 wave 11).

Covers the service-forge install surface: receipts, flag family,
profile split, context-root safety, uninstall ownership guards,
non-TTY refusal, profile-change honesty and the lifecycle ops.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from api_sentinel.cli import app
from api_sentinel.installation import installations_dir

runner = CliRunner()


@pytest.fixture(autouse=True)
def _forge_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic account registry — tests never touch the real
    ``~/.forge/installations`` (the override the engine honors)."""
    monkeypatch.setenv("FORGE_HOME_OVERRIDE", str(tmp_path / ".forge-home"))


def _invoke(*args: str, env: dict[str, str] | None = None, stdin: str | None = None):
    return runner.invoke(app, list(args), input=stdin, env=env)


def _receipt(result) -> dict:
    assert result.exit_code == 0, f"exit={result.exit_code}\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# receipts — schema + stdout/persisted parity
# ---------------------------------------------------------------------------


def test_install_receipt_schema_and_persistence(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    r = _invoke("install", "--yes", "--json", "--root", str(root))
    payload = _receipt(r)
    for key in (
        "schema",
        "forge_id",
        "operation",
        "scope",
        "profile",
        "target_root",
        "dry_run",
        "status",
        "managed_files",
        "checks",
        "verification",
        "created_at",
        "created_by",
    ):
        assert key in payload, f"receipt missing {key}"
    assert payload["schema"] == "forge/InstallReceipt/v1"
    assert payload["forge_id"] == "api-sentinel-forge"
    assert payload["operation"] == "install"
    assert payload["status"] == "completed"
    statuses = {c["status"] for c in payload["checks"]}
    assert statuses <= {"PASS", "FAIL", "BLOCKED", "UNVERIFIED", "NOT_APPLICABLE"}
    # stdout/persistence parity — the persisted receipt equals stdout
    receipts = sorted((root / ".api-sentinel" / "install" / "receipts").glob("*.json"))
    assert len(receipts) == 1
    persisted = json.loads(receipts[0].read_text())
    for key in ("schema", "forge_id", "operation", "status", "checks", "verification"):
        assert persisted[key] == payload[key]
    # the token SECRET never enters the receipt (secrets hygiene) —
    # only its path is metadata
    secret = (root / ".api-sentinel" / "token").read_text().strip()
    assert secret and secret not in json.dumps(payload)
    assert secret not in receipts[0].read_text()


def test_install_dry_run_plans_without_writes(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    r = _invoke("install", "--dry-run", "--json", "--root", str(root))
    payload = _receipt(r)
    assert payload["status"] == "planned"
    assert payload["verification"]["status"] == "UNVERIFIED"
    assert payload["dry_run"] is True
    assert payload["planned_files"]
    assert not (root / ".api-sentinel").exists()


def test_repeated_install_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    token1 = (root / ".api-sentinel" / "token").read_text()
    r2 = _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    assert r2["status"] == "completed"
    assert (root / ".api-sentinel" / "token").read_text() == token1  # reused, not recreated


# ---------------------------------------------------------------------------
# profile split — install tier vs runtime profile (honesty)
# ---------------------------------------------------------------------------


def test_profile_split_tier_vs_runtime(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    payload = _receipt(
        _invoke(
            "install",
            "--yes",
            "--json",
            "--root",
            str(root),
            "--profile",
            "full",
            "--runtime-profile",
            "aws",
        )
    )
    assert payload["profile"] == "full"  # install tier
    assert payload["components"]["runtime_profile"] == "aws"
    cfg = (root / ".api-sentinel" / "config.yaml").read_text()
    assert "profile: aws" in cfg
    assert "install_profile: full" in cfg


def test_deprecated_profile_alias_warns_and_honors(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    r = _invoke("install", "--yes", "--json", "--root", str(root), "--profile", "aws")
    payload = _receipt(r)
    assert payload["components"]["runtime_profile"] == "aws"
    assert any("deprecated" in w for w in payload["warnings"])


def test_profile_change_is_journaled_honestly(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    payload = _receipt(
        _invoke("install", "--yes", "--json", "--root", str(root), "--runtime-profile", "aws")
    )
    note = payload["runtime_profile"]
    assert note["previous_profile"] == "local"
    assert note["effective_profile"] == "aws"
    assert note["updated"] is True
    assert "profile: aws" in (root / ".api-sentinel" / "config.yaml").read_text()


def test_full_is_never_a_runtime_profile(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    r = _invoke("install", "--yes", "--json", "--root", str(root), "--runtime-profile", "full")
    assert r.exit_code == 2
    assert "runtime profile" in (r.stderr or r.stdout)


def test_unknown_profile_is_a_usage_error(tmp_path: Path) -> None:
    r = _invoke("install", "--yes", "--root", str(tmp_path / "p"), "--profile", "banana")
    assert r.exit_code == 2


# ---------------------------------------------------------------------------
# flag family — delegated argv acceptance
# ---------------------------------------------------------------------------


def test_delegated_argv_accepted(tmp_path: Path) -> None:
    """The manifest-declared flags are all real argv — The Forge's argv
    builder must never hit an unknown option."""
    root = tmp_path / "proj"
    payload = _receipt(
        _invoke(
            "install",
            "--yes",
            "--json",
            "--root",
            str(root),
            "--scope",
            "project",
            "--host",
            "claude,devin",
            "--components",
            "otel,grafana",
            "--gateway-profile",
            "deep",
            "--runtime-profile",
            "local",
            "--profile",
            "minimal",
        )
    )
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["host-assets"]["status"] == "NOT_APPLICABLE"
    assert by_id["components"]["status"] == "NOT_APPLICABLE"
    assert payload["components"]["gateway_profile"] == "deep"
    assert payload["components"]["requested"] == ["otel", "grafana"]
    assert payload["profile"] == "minimal"


def test_unknown_host_and_gateway_refused(tmp_path: Path) -> None:
    r = _invoke("install", "--yes", "--root", str(tmp_path / "p"), "--host", "emacs")
    assert r.exit_code == 2
    r = _invoke(
        "install", "--yes", "--root", str(tmp_path / "p"), "--gateway-profile", "x"
    )
    assert r.exit_code == 2


def test_root_and_workspace_are_alternatives(tmp_path: Path) -> None:
    r = _invoke(
        "install", "--yes", "--root", str(tmp_path / "a"), "--workspace", str(tmp_path / "b")
    )
    assert r.exit_code == 2
    assert "conflicting targets" in (r.stderr or r.stdout)


# ---------------------------------------------------------------------------
# context-root safety
# ---------------------------------------------------------------------------


def test_arbitrary_state_dir_never_writes_context(tmp_path: Path) -> None:
    """--workspace /tmp/x (not .api-sentinel-shaped) must not write
    /tmp/AGENTS.md or /tmp/.forge/context — the old ws.root.parent bug."""
    state = tmp_path / "weird-state"
    payload = _receipt(_invoke("install", "--yes", "--json", "--workspace", str(state)))
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["context-registry"]["status"] == "NOT_APPLICABLE"
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / ".forge").exists()
    assert any("not named" in w for w in payload.get("warnings", ()))


def test_sentinel_shaped_state_dir_derives_context_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    payload = _receipt(
        _invoke("install", "--yes", "--json", "--workspace", str(root / ".api-sentinel"))
    )
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["context-registry"]["status"] == "PASS"
    assert (root / ".forge" / "context" / "capabilities" / "api-sentinel-forge.json").exists()


def test_context_registration_and_repair(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    cap = root / ".forge" / "context" / "capabilities" / "api-sentinel-forge.json"
    registry = root / ".forge" / "context" / "registry.json"
    assert cap.exists() and registry.exists()
    reg = json.loads(registry.read_text())
    assert any(f.get("forge_id") == "api-sentinel-forge" for f in reg.get("forges", []))
    # damage → repair converges the registration back
    cap.unlink()
    r = _invoke(
        "install", "repair", "--yes", "--json", "--root", str(root)
    )
    payload = _receipt(r)
    assert payload["operation"] == "repair"
    assert cap.exists()
    status = _invoke("install", "status", "--json", "--root", str(root))
    assert json.loads(status.stdout)["status"] == "healthy"


def test_user_scope_skips_context(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("FORGE_HOME_OVERRIDE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    payload = _receipt(_invoke("install", "--yes", "--json", "--scope", "user"))
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["context-registry"]["status"] == "NOT_APPLICABLE"
    assert not (fake_home / "AGENTS.md").exists()


# ---------------------------------------------------------------------------
# workspace scope
# ---------------------------------------------------------------------------


def test_workspace_scope_marks_members_deferred(tmp_path: Path) -> None:
    wk = tmp_path / "wks"
    (wk / "member-a").mkdir(parents=True)
    (wk / "member-a" / "pyproject.toml").write_text("[project]\nname='a'\n")
    (wk / "member-b" / ".git").mkdir(parents=True)
    (wk / "forge.workspace.yaml").write_text("name: wks\n")
    payload = _receipt(
        _invoke(
            "install",
            "--yes",
            "--json",
            "--scope",
            "workspace",
            "--root",
            str(wk),
        )
    )
    assert payload["scope"] == "workspace"
    members = payload["workspace_members"]
    assert members["count"] == 2
    assert "deferred" in members["install"]
    # members were discovered read-only — no state dirs created inside them
    assert not (wk / "member-a" / ".api-sentinel").exists()
    assert not (wk / "member-b" / ".api-sentinel").exists()


# ---------------------------------------------------------------------------
# non-interactive refusal
# ---------------------------------------------------------------------------


def test_non_tty_install_requires_yes(tmp_path: Path) -> None:
    # CliRunner pipes stdin → isatty() is False → named refusal, exit 2
    r = _invoke("install", "--root", str(tmp_path / "p"))
    assert r.exit_code == 2
    assert "non-interactive" in (r.stderr or r.stdout)
    assert not (tmp_path / "p" / ".api-sentinel").exists()


def test_non_tty_uninstall_requires_yes(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    r = _invoke("uninstall", "--root", str(root))
    assert r.exit_code == 2
    assert (root / ".api-sentinel").exists()  # nothing removed


# ---------------------------------------------------------------------------
# uninstall — ownership guards + purge
# ---------------------------------------------------------------------------


def test_uninstall_keeps_data_by_default(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    payload = _receipt(_invoke("uninstall", "--yes", "--json", "--root", str(root)))
    assert payload["status"] == "completed"
    assert (root / ".api-sentinel" / "sentinel.db").exists()  # kept
    assert (root / ".api-sentinel" / "config.yaml").exists()
    assert not (root / ".forge" / "context" / "capabilities" / "api-sentinel-forge.json").exists()
    assert any("context" in r for r in payload.get("removed", ()))


def test_uninstall_purge_removes_state(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    payload = _receipt(
        _invoke("uninstall", "--yes", "--json", "--root", str(root), "--purge")
    )
    assert payload["status"] == "completed"
    assert payload["purge"] is True
    assert not (root / ".api-sentinel").exists()


def test_uninstall_refuses_unmarked_dir(tmp_path: Path) -> None:
    innocent = tmp_path / "innocent"
    innocent.mkdir()
    (innocent / "userfile.txt").write_text("precious\n")
    r = _invoke("uninstall", "--yes", "--workspace", str(innocent), "--purge")
    assert r.exit_code == 2
    assert "not a sentinel workspace" in (r.stderr or r.stdout)
    assert (innocent / "userfile.txt").exists()


def test_uninstall_refuses_filesystem_and_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGE_HOME_OVERRIDE", str(tmp_path / "fh"))
    r = _invoke("uninstall", "--yes", "--workspace", "/")
    assert r.exit_code == 2


def test_uninstall_dry_run_plans(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    payload = _receipt(_invoke("uninstall", "--yes", "--json", "--dry-run", "--root", str(root)))
    assert payload["status"] == "planned"
    assert (root / ".api-sentinel").exists()


def test_update_refuses_uninitialized_dir(tmp_path: Path) -> None:
    bare = tmp_path / "notinstalled"
    bare.mkdir()
    r = _invoke("update", "--workspace", str(bare))
    assert r.exit_code == 2
    assert not (bare / "sentinel.db").exists()  # the buggy guard used to create it


# ---------------------------------------------------------------------------
# lifecycle ops + mcp verify
# ---------------------------------------------------------------------------


def test_install_status_and_doctor(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    status = json.loads(_invoke("install", "status", "--json", "--root", str(root)).stdout)
    assert status["schema"] == "forge/InstallationHealth/v1"
    assert status["status"] == "healthy"
    assert status["installed"] is True
    doctor = json.loads(_invoke("install", "doctor", "--json", "--root", str(root)).stdout)
    ids = {c["id"] for c in doctor["checks"]}
    assert {"workspace-state", "database", "migrations", "token", "python", "mcp-handshake"} <= ids


def test_install_mcp_verify_honest(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    r = _invoke("install", "mcp-verify", "--json", "--root", str(root))
    doc = json.loads(r.stdout)
    check = doc["checks"][0]
    # honest whichever way the env resolves: missing exe/extra is a named
    # state, a real handshake is PASS — never a fabricated success
    assert check["status"] in ("PASS", "FAIL", "BLOCKED", "UNVERIFIED")
    if check["status"] == "PASS":
        assert "sentinel_health" in check.get("tools", ())
        assert r.exit_code == 0
    else:
        assert r.exit_code == 1


def test_install_repair_refuses_nothing_to_repair(tmp_path: Path) -> None:
    r = _invoke("install", "repair", "--yes", "--json", "--root", str(tmp_path / "absent"))
    assert r.exit_code == 2


def test_install_uninstall_subcommand_parity(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    payload = _receipt(
        _invoke("install", "uninstall", "--yes", "--json", "--root", str(root))
    )
    assert payload["operation"] == "uninstall"
    assert payload["status"] == "completed"


def test_account_manifest_registered(tmp_path: Path, monkeypatch) -> None:
    fake_home = tmp_path / "fh"
    fake_home.mkdir()
    monkeypatch.setenv("FORGE_HOME_OVERRIDE", str(fake_home))
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    manifest = installations_dir() / "api-sentinel-forge.json"
    assert manifest.exists()
    doc = json.loads(manifest.read_text())
    assert doc["schema"] == "forge/InstallationManifest/v1"
    assert doc["forge_id"] == "api-sentinel-forge"
    assert doc["installation_kind"] == "service"
    assert doc["mcp"]["verify_tool"] == "sentinel_health"
    assert "install" in doc and doc["depends_on"] == []


# ---------------------------------------------------------------------------
# migrations negative cases (preserved contract)
# ---------------------------------------------------------------------------


def test_migrations_check_in_receipt(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    payload = _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    mig = next(c for c in payload["checks"] if c["id"] == "migrations")
    assert mig["status"] == "PASS"


def test_db_corruption_reports_failed_check(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _receipt(_invoke("install", "--yes", "--json", "--root", str(root)))
    (root / ".api-sentinel" / "sentinel.db").write_bytes(b"not a sqlite db")
    status = json.loads(_invoke("install", "status", "--json", "--root", str(root)).stdout)
    mig = next(c for c in status["checks"] if c["id"] == "migrations")
    assert mig["status"] in ("FAIL", "UNVERIFIED")
    assert status["status"] in ("degraded", "broken", "unverified")


# ---------------------------------------------------------------------------
# bundled manifest parity — wheels cannot read the checkout root
# ---------------------------------------------------------------------------


def test_bundled_manifests_match_repo_root() -> None:
    repo = Path(__file__).resolve().parents[2]
    bundled = repo / "src" / "api_sentinel" / "installation" / "assets" / "manifest"
    for name in ("forge.json", "forge.agentic.json"):
        assert json.loads((bundled / name).read_text()) == json.loads(
            (repo / name).read_text()
        ), f"packaged {name} drifted from the repo root copy"


def test_manifest_install_surface_matches_cli() -> None:
    """Every operation/flag the manifest declares is real on the CLI —
    the orchestrator's argv builder trusts the declaration."""
    repo = Path(__file__).resolve().parents[2]
    manifest = json.loads((repo / "forge.json").read_text())
    install = manifest["install"]
    assert manifest["installation_kind"] == "service"
    assert manifest["mcp_verify_tool"] == "sentinel_health"
    assert manifest["depends_on"] == []
    for op in ("status", "doctor", "repair", "uninstall", "mcp-verify"):
        assert op in install["operations"]
    for flag in (
        "--scope",
        "--root",
        "--workspace",
        "--host",
        "--profile",
        "--runtime-profile",
        "--components",
        "--gateway-profile",
        "--yes",
        "--dry-run",
        "--purge",
        "--json",
    ):
        assert flag in install["flags"]
    assert install["host_assets"] is False
