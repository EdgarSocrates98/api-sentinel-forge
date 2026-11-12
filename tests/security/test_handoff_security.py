"""Cycle-10 §5.2 — handoff/file-boundary hardening.

Every workspace-managed write derives a slot from an id that *could* come
from a peer's envelope; every peer-supplied read is bounded. These tests
prove traversal, symlink escapes and oversized inputs are typed refusals
— never silent writes outside the authorized root.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.domain import ContractError, ValidationError
from api_sentinel.infrastructure.security.paths import (
    MAX_INBOUND_BYTES,
    read_text_bounded,
    resolve_under_root,
    safe_child_name,
    safe_slot,
)
from api_sentinel.integrations.doctor.adapter import DoctorACL, DoctorMode
from api_sentinel.integrations.receipts import (
    DelegationReceipt,
    write_receipt,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _handoff(handoff_id: str) -> InvestigationHandoffV1:
    return InvestigationHandoffV1(
        schema_=InvestigationHandoffV1.expected_schema,
        handoff_id=handoff_id,
        from_forge="api-sentinel-forge",
        to_forge="forge-doctor-api",
        kind="api-investigation",
        payload={"service_id": "svc_lab"},
        constraints={},
    )


class TestPathGuards:
    @pytest.mark.parametrize(
        "bad",
        [
            "..",
            "../escape",
            "a/b",
            "a\\b",
            "/abs",
            "C:\\win",
            ".hidden",
            "",
            "~home",
            "a:b",
        ],
    )
    def test_safe_child_name_refuses_traversal(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            safe_child_name(bad)

    def test_resolve_under_root_refuses_literal_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "handoffs"
        root.mkdir()
        with pytest.raises(ValidationError):
            resolve_under_root(root, root / ".." / "outside")

    def test_resolve_under_root_refuses_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "handoffs"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = root / "planted"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation not permitted")
        with pytest.raises(ValidationError):
            resolve_under_root(root, link / "file.json")

    def test_safe_slot_accepts_a_normal_id(self, tmp_path: Path) -> None:
        root = tmp_path / "handoffs"
        root.mkdir()
        slot = safe_slot(root, "hnd_abc123")
        assert slot.parent == root.resolve()
        assert slot.name == "hnd_abc123"


class TestBoundedReads:
    def test_oversized_peer_file_is_refused(self, tmp_path: Path) -> None:
        big = tmp_path / "bundle.json"
        big.write_bytes(b"x" * (MAX_INBOUND_BYTES + 1))
        with pytest.raises(ValidationError, match="inbound limit"):
            read_text_bounded(big)

    def test_missing_peer_file_is_typed(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            read_text_bounded(tmp_path / "absent.json")


class TestReceiptBoundary:
    def test_receipt_filename_cannot_traverse(self, tmp_path: Path) -> None:
        receipt = DelegationReceipt(
            receipt_id="../evil",
            to_forge="forge-doctor-api",
            kind="api-investigation",
            outcome="delegated",
            at=NOW,
        )
        with pytest.raises(ValidationError):
            write_receipt(tmp_path / "receipts", receipt)

    def test_receipt_carries_correlation_and_state_split(self, tmp_path: Path) -> None:
        receipt = DelegationReceipt(
            receipt_id="rcpt_hnd_lab1",
            to_forge="forge-doctor-api",
            kind="api-investigation",
            outcome="delegated",
            at=NOW,
            handoff_id="hnd_lab1",
            correlation_id="cor_lab1",
            transport_state="emitted",
            execution_state="pending",
        )
        path = write_receipt(tmp_path / "receipts", receipt)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["correlation_id"] == "cor_lab1"
        # the transport emitted an envelope; nothing executed remotely —
        # the two axes stay distinct on disk
        assert data["transport_state"] == "emitted"
        assert data["execution_state"] == "pending"


class TestDoctorHandoffBoundary:
    def test_crafted_handoff_id_cannot_escape(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        handoffs = tmp_path / "handoffs"
        with pytest.raises(ValidationError):
            acl.delegate(
                _handoff("../escape"),
                target=str(tmp_path / "ctx"),
                handoffs_dir=handoffs,
            )
        assert not (tmp_path / "escape").exists()

    def test_handoff_emits_under_root_with_correlation(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        result = acl.delegate(
            _handoff("hnd_lab_sec1"),
            target=str(tmp_path / "ctx"),
            handoffs_dir=tmp_path / "handoffs",
        )
        assert result.status == "delegated"
        emitted = tmp_path / "handoffs" / "hnd_lab_sec1" / "doctor_request.json"
        assert emitted.is_file()

    def test_ingest_correlation_mismatch_is_refused(self, tmp_path: Path) -> None:
        bundle = tmp_path / "bundle.json"
        bundle.write_text(
            json.dumps({"handoff_version": 2, "handoff_id": "hnd_other", "findings": []}),
            encoding="utf-8",
        )
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        with pytest.raises(ContractError, match="correlation mismatch"):
            acl.ingest(bundle, expected_handoff_id="hnd_mine")

    def test_ingest_oversized_bundle_is_refused(self, tmp_path: Path) -> None:
        big = tmp_path / "bundle.json"
        big.write_bytes(b" " * (MAX_INBOUND_BYTES + 1))
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        with pytest.raises(ValidationError):
            acl.ingest(big)


class TestSessionSlotBoundary:
    def test_crafted_session_request_id_cannot_escape(self, tmp_path: Path) -> None:
        from api_sentinel.integrations.bridge.adapter import (
            FileAgentSession,
        )
        from api_sentinel.investigation.agent_session import (
            AgentSessionRequest,
            SessionGovernance,
        )
        from api_sentinel.investigation.context_pack import (
            ContextPack,
            PackInstructions,
        )

        sessions = FileAgentSession(tmp_path / "agent_sessions")
        request = AgentSessionRequest(
            session_request_id="../escape",
            incident_id="inc_lab",
            pack_id="pack_lab",
            instructions=PackInstructions(text="investigate inc_lab"),
            context_pack=ContextPack(
                pack_id="pack_lab",
                incident_id="inc_lab",
                built_at=NOW,
                budget_bytes=1024,
                size_bytes=0,
                instructions=PackInstructions(text="investigate inc_lab"),
            ),
            governance=SessionGovernance(principal="lab", workspace="ws"),
        )
        with pytest.raises(ValidationError):
            sessions.open_session(request)
        assert not (tmp_path / "escape").exists()
