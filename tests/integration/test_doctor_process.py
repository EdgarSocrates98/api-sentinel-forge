"""Cycle 10.1 — Doctor process-isolation battery (C101-F001).

The delegation budget is now a *hard* wall-clock bound: a blocked Doctor
child is terminated, then killed — never waited on. Every adversary below
produces a named terminal state (timed_out | failed | unavailable |
refused), a dead child, and a receipt — never a hang, a fabricated
bundle, or an orphan.
"""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.contracts.v1.models import InvestigationHandoffV1
from api_sentinel.integrations.doctor.adapter import (
    DoctorACL,
    DoctorMode,
    doctor_importable,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)

_LAB_SLEEP = "import time\ntime.sleep(600)\n"
_LAB_SLEEP_IGNORING_TERM = (
    "import signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "time.sleep(600)\n"
)
_LAB_CRASH = "import sys\nsys.stderr.write('boom')\nsys.exit(7)\n"
_LAB_GARBAGE_BUNDLE = (
    "import sys, pathlib\n"
    "out = pathlib.Path(sys.argv[2])\n"
    "(out / 'bundle.json').write_text('{not json')\n"
    "(out / 'meta.json').write_text('{\"status\": \"completed\"}')\n"
)
_LAB_OVERSIZE_BUNDLE = (
    "import sys, pathlib\n"
    "out = pathlib.Path(sys.argv[2])\n"
    "(out / 'bundle.json').write_bytes(b'x' * (9 * 1024 * 1024))\n"
    "(out / 'meta.json').write_text('{\"status\": \"completed\"}')\n"
)
_LAB_LEAK_STDERR = (
    "import sys\n"
    "sys.stderr.write('aws_secret_access_key=AKIAIOSFODNN7EXAMPLE\\n')\n"
    "sys.exit(5)\n"
)
_LAB_OK = (
    "import json, sys, pathlib\n"
    "out = pathlib.Path(sys.argv[2])\n"
    "bundle = {'handoff_version': 2, 'handoff_id': 'hf_p1', "
    "'analysis_rev': 'rev0', 'findings': [], 'unknowns': []}\n"
    "(out / 'bundle.json').write_text(json.dumps(bundle))\n"
    "(out / 'meta.json').write_text('{\"status\": \"completed\"}')\n"
)


def _handoff(**over) -> InvestigationHandoffV1:
    fields: dict = {
        "schema": "api-sentinel/InvestigationHandoff/v1",
        "handoff_id": "hf_p1",
        "from_forge": "api-sentinel-forge",
        "to_forge": "forge-doctor-api",
        "kind": "investigation",
        "payload": {"service_id": "svc_x"},
        "constraints": {},
    }
    fields.update(over)
    return InvestigationHandoffV1(**fields)


def _lab_acl(
    monkeypatch, tmp_path: Path, script: str, *, timeout: float = 2.0
) -> DoctorACL:
    """ACL whose worker argv points at a lab script instead of the worker."""
    lab = tmp_path / "lab_worker.py"
    lab.write_text(script, encoding="utf-8")
    monkeypatch.setattr(
        "api_sentinel.integrations.doctor.adapter.doctor_importable",
        lambda: True,
    )
    acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=timeout)

    def _argv(request_path: Path, out_dir: Path) -> list[str]:
        return [sys.executable, str(lab), str(request_path), str(out_dir)]

    monkeypatch.setattr(acl, "_worker_argv", _argv)
    return acl


class TestHardTimeout:
    def test_blocked_child_is_timed_out_and_dead(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        acl = _lab_acl(monkeypatch, tmp_path, _LAB_SLEEP, timeout=0.5)
        started = time.monotonic()
        result = acl.delegate(
            _handoff(), target="t", context_root=tmp_path,
            receipts_dir=tmp_path / "receipts",
        )
        elapsed = time.monotonic() - started
        assert result.status == "timed_out", result.detail
        assert "execution_timeout" in result.detail
        # budget + terminate/kill grace + slack — strictly bounded
        assert elapsed < 15.0
        receipts = list((tmp_path / "receipts").glob("*.json"))
        assert len(receipts) == 1
        assert '"outcome":"timed_out"' in receipts[0].read_text()

    def test_child_ignoring_sigterm_is_killed(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        acl = _lab_acl(
            monkeypatch, tmp_path, _LAB_SLEEP_IGNORING_TERM, timeout=0.5
        )
        result = acl.delegate(
            _handoff(), target="t", context_root=tmp_path
        )
        assert result.status == "timed_out"
        # POSIX: SIGTERM ignored → escalation recorded; Windows: the
        # terminate call is already forceful — either way the child is dead
        # and the state is terminal.
        assert "terminated" in result.detail or "killed" in result.detail

    def test_next_delegation_works_after_timeout(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        # resource cleanup: a killed child must not poison the next call
        acl = _lab_acl(monkeypatch, tmp_path, _LAB_SLEEP, timeout=0.5)
        first = acl.delegate(_handoff(), target="t", context_root=tmp_path)
        assert first.status == "timed_out"
        (tmp_path / "ok").mkdir(exist_ok=True)
        ok_acl = _lab_acl(monkeypatch, tmp_path / "ok", _LAB_OK)
        second = ok_acl.delegate(
            _handoff(), target="t", context_root=tmp_path
        )
        assert second.status == "completed"
        assert second.ingest is not None

    def test_concurrent_delegations_isolated(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        import concurrent.futures

        acl = _lab_acl(monkeypatch, tmp_path, _LAB_SLEEP, timeout=0.5)
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(
                pool.map(
                    lambda i: acl.delegate(
                        _handoff(handoff_id=f"hf_c{i}"),
                        target="t",
                        context_root=tmp_path,
                    ),
                    range(3),
                )
            )
        failures = [
            (r.status, r.detail) for r in results if r.status != "timed_out"
        ]
        assert not failures, failures


class TestWorkerFailureModes:
    def test_crashing_child_named_failed(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        acl = _lab_acl(monkeypatch, tmp_path, _LAB_CRASH)
        result = acl.delegate(_handoff(), target="t", context_root=tmp_path)
        assert result.status == "failed"
        assert "provider_failed" in result.detail

    def test_malformed_bundle_named_failed(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        acl = _lab_acl(monkeypatch, tmp_path, _LAB_GARBAGE_BUNDLE)
        result = acl.delegate(_handoff(), target="t", context_root=tmp_path)
        assert result.status == "failed"
        assert "invalid_response" in result.detail

    def test_oversized_bundle_named_failed(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        acl = _lab_acl(monkeypatch, tmp_path, _LAB_OVERSIZE_BUNDLE)
        result = acl.delegate(_handoff(), target="t", context_root=tmp_path)
        assert result.status == "failed"
        assert "invalid_response" in result.detail

    def test_secret_in_child_stderr_is_redacted(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        acl = _lab_acl(monkeypatch, tmp_path, _LAB_LEAK_STDERR)
        result = acl.delegate(_handoff(), target="t", context_root=tmp_path)
        assert result.status == "failed"
        assert "AKIAIOSFODNN7EXAMPLE" not in result.detail
        assert "REDACTED" in result.detail or "aws_secret" in result.detail

    def test_credentials_not_forwarded_to_child(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI")
        monkeypatch.setenv("FORGE_TOKEN", "topsecret")
        acl = _lab_acl(monkeypatch, tmp_path, _LAB_OK)
        env = acl._child_env()
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "FORGE_TOKEN" not in env


class TestNamedStatesPreserved:
    def test_missing_context_root_refused(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=5)
        result = acl.delegate(
            _handoff(),
            target="t",
            context_root=tmp_path / "absent",
        )
        assert result.status == "refused"

    def test_no_context_root_refused(self) -> None:
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=5)
        result = acl.delegate(_handoff(), target="t", context_root=None)
        assert result.status == "refused"


@pytest.mark.skipif(
    not doctor_importable(),
    reason="forge_doctor_api not importable in this interpreter",
)
class TestRealWorker:
    def test_real_doctor_completes_via_child(
        self, tmp_path: Path
    ) -> None:
        lab = (
            Path(__file__).resolve().parents[2]
            / "lab" / "fixtures" / "doctor-project"
        )
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=60)
        result = acl.delegate(
            _handoff(),
            target=str(lab),
            context_root=lab,
            receipts_dir=tmp_path / "receipts",
        )
        assert result.status == "completed", result.detail
        assert result.ingest is not None
        assert "isolated child process" in result.detail
