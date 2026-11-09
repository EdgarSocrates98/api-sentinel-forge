"""Unit tests — integration status, receipts, timeout named states."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from api_sentinel.integrations.receipts import DelegationReceipt, write_receipt
from api_sentinel.integrations.status import IntegrationState, integration_status

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "lab/fixtures/lab-003-dependency-cascade/atlas-export.json"
)


class TestIntegrationStatus:
    def test_states_are_named_never_crashed(self) -> None:
        states = integration_status()
        by_forge = {s["forge"]: s["state"] for s in states}
        # the-forge boundary is always available — it IS this process.
        assert by_forge["the-forge"] == IntegrationState.AVAILABLE.value
        # atlas with no manifest → unavailable, never fabricated.
        assert by_forge["code-atlas-forge"] in (
            IntegrationState.UNAVAILABLE.value,
            IntegrationState.UNVERIFIED.value,
        )
        # doctor — available (importable) or unverified (handoff_file).
        assert by_forge["forge-doctor-api"] in (
            IntegrationState.AVAILABLE.value,
            IntegrationState.UNVERIFIED.value,
        )

    def test_atlas_with_valid_manifest_available(self) -> None:
        states = integration_status(atlas_manifest=FIXTURE)
        atlas = next(s for s in states if s["forge"] == "code-atlas-forge")
        assert atlas["state"] == IntegrationState.AVAILABLE.value

    def test_atlas_absent_manifest_unavailable_named(self, tmp_path: Path) -> None:
        states = integration_status(atlas_manifest=tmp_path / "missing.json")
        atlas = next(s for s in states if s["forge"] == "code-atlas-forge")
        assert atlas["state"] == IntegrationState.UNAVAILABLE.value
        assert "manifest" in atlas["reason"].lower()

    def test_sorted_by_forge(self) -> None:
        names = [s["forge"] for s in integration_status()]
        assert names == sorted(names)


class TestReceipts:
    def test_receipt_writes_redacted_json(self, tmp_path: Path) -> None:
        receipt = DelegationReceipt(
            receipt_id="rcpt_1",
            to_forge="forge-doctor-api",
            kind="api-investigation",
            outcome="delegated",
            at=NOW,
            handoff_id="hnd_1",
            detail="token=sk-secret-value-12345 emitted",
        )
        path = write_receipt(tmp_path, receipt)
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["to_forge"] == "forge-doctor-api"
        assert data["outcome"] == "delegated"
        # Secret masked — never persisted verbatim (token= matched).
        raw = path.read_text(encoding="utf-8")
        assert "sk-secret-value-12345" not in raw
        assert "[REDACTED]" in raw

    def test_receipt_carries_audit_fields(self, tmp_path: Path) -> None:
        receipt = DelegationReceipt(
            receipt_id="rcpt_2",
            to_forge="code-atlas-forge",
            kind="structural-context",
            outcome="unavailable",
            at=NOW,
            handoff_id="hnd_2",
            request_id="hnd_2",
            duration_ms=1.5,
            evidence_refs=["ref_a", "ref_b"],
        )
        write_receipt(tmp_path, receipt)
        data = json.loads((tmp_path / "rcpt_2.receipt.json").read_text())
        assert data["forge" if "forge" in data else "to_forge"] == "code-atlas-forge"
        assert data["duration_ms"] == 1.5
        assert data["evidence_refs"] == ["ref_a", "ref_b"]
