"""Cycle 10.1 — evidence integrity battery (C101-F002).

The federated receipt must embed every step *including S18 itself*, the
manifest must hash the final bytes of every artifact, and the independent
verifier must catch any disagreement — tampering, truncation, drift.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LAB = Path(__file__).resolve().parents[2] / "lab"
sys.path.insert(0, str(LAB))

import federated_e2e as fe  # noqa: E402

VERIFIER = LAB / "verify_evidence.py"


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An isolated run root: steps + artifacts + evidence under tmp."""
    (tmp_path / "lab").mkdir(parents=True)
    shutil.copy(VERIFIER, tmp_path / "lab" / "verify_evidence.py")
    steps_dir = tmp_path / "steps"
    art_dir = tmp_path / "artifacts"
    ev_dir = tmp_path / "evidence"
    steps_dir.mkdir()
    art_dir.mkdir()
    monkeypatch.setattr(fe, "ROOT", tmp_path)
    monkeypatch.setattr(fe, "STEPS_DIR", steps_dir)
    monkeypatch.setattr(fe, "ART_DIR", art_dir)
    monkeypatch.setattr(fe, "EVIDENCE", ev_dir)
    monkeypatch.setattr(fe, "ORACLE", [])
    (art_dir / "demo.bin").write_bytes(b"x" * 32)
    return tmp_path


def _seed_steps(steps_dir: Path, statuses: list[str]) -> None:
    for i, status in enumerate(statuses, 1):
        record = {
            "id": f"S{i}",
            "status": status,
            "truth": "LOCAL_REAL",
            "artifacts": [],
            "hashes": {},
            "errors": [],
            "output": {},
        }
        fe.ORACLE.append(record)
        (steps_dir / f"S{i}.json").write_text(json.dumps(record))


def _run_verifier(root: Path, ev: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, lab verifier script
        [
            sys.executable,
            str(root / "lab" / "verify_evidence.py"),
            "--receipt",
            str(ev / "federated-e2e-receipt.json"),
            "--manifest",
            str(ev / "federated-e2e-manifest.json"),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestSelfIncludingReceipt:
    def test_receipt_embeds_all_steps_including_s18(
        self, sandbox: Path
    ) -> None:
        steps_dir = sandbox / "steps"
        _seed_steps(steps_dir, ["pass", "pass", "blocked"])
        receipt_path = fe.s18_receipt(sandbox / "ws")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        ids = [s["id"] for s in receipt["steps"]]
        assert ids == ["S1", "S2", "S3", "S18"]
        # S18's embedded record is identical to its on-disk step file
        on_disk = json.loads((steps_dir / "S18.json").read_text())
        assert receipt["steps"][-1] == on_disk
        assert receipt["summary"]["total"] == 4
        assert receipt["summary"]["blocked"] == 1

    def test_manifest_hashes_every_artifact(self, sandbox: Path) -> None:
        _seed_steps(sandbox / "steps", ["pass"])
        fe.s18_receipt(sandbox / "ws")
        manifest = json.loads(
            (sandbox / "evidence" / "federated-e2e-manifest.json")
            .read_text(encoding="utf-8")
        )
        paths = {e["path"].replace("\\", "/") for e in manifest["files"]}
        assert any(p.endswith("steps/S18.json") for p in paths)
        assert any(
            p.endswith("federated-e2e-receipt.json") for p in paths
        )
        assert any(p.endswith("artifacts/demo.bin") for p in paths)


class TestVerifierAdversarial:
    def _run(self, sandbox: Path) -> Path:
        _seed_steps(sandbox / "steps", ["pass", "pass"])
        return fe.s18_receipt(sandbox / "ws")

    def test_tampered_step_file_detected(self, sandbox: Path) -> None:
        self._run(sandbox)
        (sandbox / "steps" / "S1.json").write_text(
            '{"id": "S1", "status": "HAX"}'
        )
        v = _run_verifier(sandbox, sandbox / "evidence")
        assert v.returncode == 1
        assert "sha256 mismatch" in v.stdout
        assert "receipt record != step file" in v.stdout

    def test_tampered_receipt_detected(self, sandbox: Path) -> None:
        receipt_path = self._run(sandbox)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["summary"]["passed"] = 99
        receipt_path.write_text(json.dumps(receipt))
        v = _run_verifier(sandbox, sandbox / "evidence")
        assert v.returncode == 1
        assert "sha256 mismatch" in v.stdout
        assert "summary.passed" in v.stdout

    def test_missing_artifact_detected(self, sandbox: Path) -> None:
        self._run(sandbox)
        (sandbox / "artifacts" / "demo.bin").unlink()
        v = _run_verifier(sandbox, sandbox / "evidence")
        assert v.returncode == 1
        assert "missing on disk" in v.stdout

    def test_unmanifested_evidence_detected(self, sandbox: Path) -> None:
        self._run(sandbox)
        (sandbox / "steps" / "S99.json").write_text(
            '{"id": "S99", "status": "pass"}'
        )
        v = _run_verifier(sandbox, sandbox / "evidence")
        assert v.returncode == 1
        assert "not in manifest" in v.stdout

    def test_clean_run_verifies(self, sandbox: Path) -> None:
        self._run(sandbox)
        v = _run_verifier(sandbox, sandbox / "evidence")
        assert v.returncode == 0
        assert "verification: OK" in v.stdout
