"""Cycle 10.3 regression tests for portability, evidence and redaction."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

LAB = Path(__file__).resolve().parents[2] / "lab"
sys.path.insert(0, str(LAB))

from federated_e2e import semantic_row_differences  # noqa: E402
from runtime import resolve_entrypoint, resolve_python  # noqa: E402
from verify_evidence import main as verify_main  # noqa: E402

from api_sentinel.integrations.bridge.adapter import (  # noqa: E402
    HttpAgentSession,
)
from api_sentinel.tui.catalog import (  # noqa: E402
    Command,
    Field,
    build_argv,
    display_argv,
)


def _bundle(tmp_path: Path, rel: str = "steps/S1.json") -> tuple[Path, Path, Path]:
    root = tmp_path / "bundle"
    target = root / rel
    target.parent.mkdir(parents=True)
    record = {"id": "S1", "status": "pass", "truth": "LOCAL_REAL"}
    target.write_text(json.dumps(record), encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    receipt = root / "federated-e2e-receipt.json"
    receipt.write_text(json.dumps({
        "schema": "sentinel-evidence/FederatedE2EReceipt/v1",
        "steps": [record],
        "summary": {"total": 1, "passed": 1, "failed": 0,
                    "blocked": 0, "not_run": 0},
    }), encoding="utf-8")
    manifest = root / "federated-e2e-manifest.json"
    manifest.write_text(json.dumps({
        "schema": "api-sentinel/EvidenceManifest/v1",
        "files": [{"path": rel, "sha256": digest,
                   "bytes": target.stat().st_size}],
    }), encoding="utf-8")
    return root, receipt, manifest


def test_runtime_resolution_supports_spaces_and_installed_entrypoint(
    tmp_path: Path,
) -> None:
    python = resolve_python(Path(__file__).resolve().parents[2])
    assert python.is_file()
    venv = tmp_path / "unicode path"
    bin_dir = venv / ("Scripts" if sys.platform == "win32" else "bin")
    bin_dir.mkdir(parents=True)
    fake = bin_dir / ("sentinel-tool.exe" if sys.platform == "win32" else "sentinel-tool")
    fake.write_text("x", encoding="utf-8")
    assert resolve_entrypoint("sentinel-tool", python=python, explicit=fake) == fake


def test_verifier_rejects_traversal_and_duplicate_paths(tmp_path: Path) -> None:
    root, receipt, manifest = _bundle(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"][0]["path"] = "../outside.json"
    data["files"].append(data["files"][0].copy())
    manifest.write_text(json.dumps(data), encoding="utf-8")
    old_argv = sys.argv
    try:
        sys.argv = ["verify", "--receipt", str(receipt), "--manifest",
                    str(manifest), "--root", str(root)]
        assert verify_main() == 1
    finally:
        sys.argv = old_argv


def test_verifier_rejects_symlink_escape(tmp_path: Path) -> None:
    root, receipt, manifest = _bundle(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = root / "steps" / "escape.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this host")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    digest = "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest()
    data["files"].append({"path": "steps/escape.json", "sha256": digest,
                           "bytes": outside.stat().st_size})
    manifest.write_text(json.dumps(data), encoding="utf-8")
    old_argv = sys.argv
    try:
        sys.argv = ["verify", "--receipt", str(receipt), "--manifest",
                    str(manifest), "--root", str(root)]
        assert verify_main() == 1
    finally:
        sys.argv = old_argv


def test_tui_display_argv_masks_sensitive_value() -> None:
    cmd = Command("security", "x", ("investigate",),
                  (Field("--token", "Token", sensitive=True),))
    execution = build_argv(cmd, {"--token": "secret-value"}, ws=None)
    display = display_argv(cmd, {"--token": "secret-value"}, ws=None)
    assert execution[-1] == "secret-value"
    assert display[-1] == "[REDACTED]"


def test_semantic_parity_rejects_same_count_different_rows() -> None:
    before = {"rows": {"incidents": [("inc-1", "open")]}}
    after = {"rows": {"incidents": [("inc-1", "resolved")]}}
    assert semantic_row_differences(before, after) == ["incidents"]


def test_bridge_http_detail_is_bounded_and_redacted() -> None:
    payload = b"{" + b"x" * ((1 << 20) + 10) + b"}"
    headers = Message()
    headers["Content-Type"] = "application/json"
    exc = HTTPError("http://127.0.0.1", 500, "server error", headers,
                    io.BytesIO(payload))
    detail = HttpAgentSession._http_detail(exc)
    assert "size limit" in detail
    assert len(detail) < 200


def test_help_is_renderable_on_windows_console() -> None:
    result = subprocess.run(  # noqa: S603 — fixed local runner argv
        [sys.executable, str(LAB / "federated_e2e.py"), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    assert "skip-traffic-sleep" in result.stdout
