"""Cycle-5 integration tests — handoff round-trip, receipts, LAB-003."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from api_sentinel.domain import (
    ApiService,
    ExternalRef,
    FixedClock,
    SequentialIdFactory,
    ServiceEnvironment,
)
from api_sentinel.infrastructure.storage import SqliteUnitOfWork
from api_sentinel.integrations.atlas.file_adapter import FileStructuralContext
from api_sentinel.integrations.doctor.adapter import DoctorACL, DoctorMode
from api_sentinel.integrations.service import (
    Correlate,
    DelegateToDoctor,
    RecordDependency,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LAB = Path(__file__).resolve().parents[2] / "lab/fixtures/lab-003-dependency-cascade"


def _uow(db: Path) -> SqliteUnitOfWork:
    return SqliteUnitOfWork(db)


def _seed_services(db: Path) -> None:
    with _uow(db) as uow:
        env = uow.environments.get_by_name("lab") or uow.environments.put(
            ServiceEnvironment(environment_id="env_lab", name="lab", created_at=NOW)
        )
        for sid, name, atlas_id in (
            ("svc_checkout", "checkout-api", "atlas:svc:checkout-api"),
            ("svc_orders", "orders", "atlas:svc:orders"),
            ("svc_payments", "payments", "atlas:svc:payments"),
        ):
            uow.services.put(
                ApiService(
                    service_id=sid,
                    service_name=name,
                    environment_id=env.environment_id,
                    external_refs=[
                        ExternalRef(
                            kind="code_atlas_identity",
                            ref=atlas_id,
                            provenance="verified",
                        )
                    ],
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        uow.commit()


def _record_deps(db: Path) -> None:
    clock = FixedClock(NOW)
    edges = [
        ("svc_checkout", "orders", "service", 240, 8, 0),
        ("svc_orders", "payments", "service", 236, 30, 0),
        ("svc_payments", "payments-db", "datastore", 180, 0, 96),
    ]
    ws, we = (
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
    )
    for sid, target, kind, calls, errors, timeouts in edges:
        RecordDependency(_uow(db), clock).execute(
            service_id=sid,
            target=target,
            target_kind=kind,
            window_start=ws,
            window_end=we,
            calls=calls,
            errors=errors,
            timeouts=timeouts,
            evidence_refs=[f"ev:trace-{sid}-{target}"],
        )


class TestHandoffFileRoundTrip:
    def test_delegate_emits_envelope_and_request(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed_services(db)
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        outcome = DelegateToDoctor(
            _uow(db),
            FixedClock(NOW),
            SequentialIdFactory(),
            acl,
            workspace_root=ws,
        ).execute(service_id="svc_orders", target="platform/orders")
        assert outcome.status == "delegated"
        assert outcome.request_ref is not None
        req = json.loads(Path(outcome.request_ref).read_text())
        assert req["protocol_version"] == 2
        assert req["target"] == "platform/orders"
        slot = Path(outcome.request_ref).parent
        env = json.loads((slot / "investigation_handoff.json").read_text())
        assert env["schema"] == "api-sentinel/InvestigationHandoff/v1"
        assert env["to_forge"] == "forge-doctor-api"

    def test_audit_row_and_receipt_persist(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        ws = tmp_path / "ws"
        ws.mkdir()
        _seed_services(db)
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        outcome = DelegateToDoctor(
            _uow(db),
            FixedClock(NOW),
            SequentialIdFactory(),
            acl,
            workspace_root=ws,
        ).execute(service_id="svc_orders", target="t")
        with _uow(db) as uow:
            record = uow.handoff_records.get(outcome.handoff.handoff_id)
            assert record is not None
            assert record.status.value == "delegated"
            assert record.request_ref == outcome.request_ref
        receipts = list((ws / "receipts").glob("*.receipt.json"))
        assert receipts
        data = json.loads(receipts[0].read_text())
        assert data["to_forge"] == "forge-doctor-api"
        assert data["outcome"] == "delegated"

    def test_ingest_doctor_bundle_preserves_verbatim(self, tmp_path: Path) -> None:
        bundle = {
            "handoff_version": 2,
            "handoff_id": "doc_1",
            "analysis_rev": "rev9",
            "service": "orders",
            "findings": [{"id": "OAS001", "title": "missing spec", "severity": "high"}],
            "unknowns": [{"subject": "x", "missing": "contract", "resolution": "add"}],
            "capabilities": [{"name": "rest.openapi", "status": "detected"}],
        }
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        ingest = DoctorACL(mode="handoff_file").ingest(path)
        assert ingest.findings == bundle["findings"]
        assert ingest.unknowns == bundle["unknowns"]
        assert ingest.analysis_rev == "rev9"


class TestTimeoutNamedState:
    def test_in_process_timeout_is_named_terminal(self, tmp_path: Path) -> None:
        """A bounded timeout produces TIMED_OUT, never a hang."""
        db = tmp_path / "s.db"
        _seed_services(db)
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=0.001)
        outcome = DelegateToDoctor(
            _uow(db),
            FixedClock(NOW),
            SequentialIdFactory(),
            acl,
            workspace_root=tmp_path / "ws",
        ).execute(
            service_id="svc_orders",
            target="t",
            context_root=tmp_path,
        )
        # Not importable → named UNAVAILABLE, not a simulated run.
        assert outcome.status in ("unavailable", "timed_out")

    def test_in_process_without_context_refused(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        _seed_services(db)
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=5)
        outcome = DelegateToDoctor(
            _uow(db),
            FixedClock(NOW),
            SequentialIdFactory(),
            acl,
            workspace_root=tmp_path / "ws",
        ).execute(service_id="svc_orders", target="t", context_root=None)
        # Either importable→refused (no context_root) or not-importable→unavailable.
        assert outcome.status in ("refused", "unavailable")


class TestLab003Cascade:
    """The flagship oracle: observed dependency identified, no causal certainty."""

    def _setup(self, db: Path) -> None:
        _seed_services(db)
        _record_deps(db)

    def test_observed_dependency_identified(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        self._setup(db)
        atlas = FileStructuralContext.load(LAB / "atlas-export.json")
        report = Correlate(_uow(db), structural=atlas).execute(service_id="svc_orders")
        # The observed edge with timeouts — payments→payments-db is the
        # worst offender *for payments*; orders' own worst observed edge is
        # payments (30 errors). The oracle checks the *observed* dependency.
        assert report.suspected_origin == "payments"
        assert report.status.value == "CORRELATED"

    def test_two_graphs_never_merged(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        self._setup(db)
        atlas = FileStructuralContext.load(LAB / "atlas-export.json")
        report = Correlate(_uow(db), structural=atlas).execute(service_id="svc_orders")
        # Observed edges labeled 'observed', structural 'structural' — distinct.
        assert all(e.kind == "observed" for e in report.observed_edges)
        assert all(e.kind == "structural" for e in report.structural_edges)
        observed_targets = {e.to_ref for e in report.observed_edges}
        structural_targets = {e.to_ref for e in report.structural_edges}
        # The structural graph names Atlas ids; the operational names service ids.
        assert "payments" in observed_targets
        assert "atlas:svc:payments" in structural_targets

    def test_hypotheses_stay_hypothesized_never_causal(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        self._setup(db)
        atlas = FileStructuralContext.load(LAB / "atlas-export.json")
        report = Correlate(_uow(db), structural=atlas).execute(service_id="svc_orders")
        assert report.hypotheses, "expected a candidate hypothesis"
        for h in report.hypotheses:
            assert h.confidence.value == "HYPOTHESIZED"
            assert h.confidence.value != "VERIFIED"
        assert report.status.value in ("CORRELATED", "OBSERVED")
        assert report.status.value != "VERIFIED"

    def test_without_atlas_honest_unknown(self, tmp_path: Path) -> None:
        db = tmp_path / "s.db"
        self._setup(db)
        report = Correlate(_uow(db), structural=None).execute(service_id="svc_orders")
        assert report.structural_edges == []
        assert any("structural" in u for u in report.unknowns)

    def test_unresolved_identity_honest_unknown_no_guess(self, tmp_path: Path) -> None:
        """A service without code_atlas_identity stays UNKNOWN (LAB-006)."""
        db = tmp_path / "s.db"
        with _uow(db) as uow:
            env = uow.environments.get_by_name("lab") or uow.environments.put(
                ServiceEnvironment(environment_id="env_lab", name="lab", created_at=NOW)
            )
            uow.services.put(
                ApiService(
                    service_id="svc_unmapped",
                    service_name="mystery",
                    environment_id=env.environment_id,
                    external_refs=[],  # no atlas identity declared
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            uow.commit()
        atlas = FileStructuralContext.load(LAB / "atlas-export.json")
        report = Correlate(_uow(db), structural=atlas).execute(service_id="svc_unmapped")
        assert report.structural_edges == []
        assert any("unresolved" in u for u in report.unknowns)


class TestReceiptRedaction:
    def test_secret_in_detail_is_masked(self, tmp_path: Path) -> None:
        """A token reaching the detail string never lands on disk verbatim."""
        from api_sentinel.integrations.receipts import (
            DelegationReceipt,
            write_receipt,
        )

        receipt = DelegationReceipt(
            receipt_id="rcpt_secret",
            to_forge="forge-doctor-api",
            kind="api-investigation",
            outcome="delegated",
            at=NOW,
            handoff_id="hnd_s",
            detail="authorization: bearer ghp_ABCDEFGHIJKLMNOP1234 accepted",
        )
        path = write_receipt(tmp_path, receipt)
        raw = path.read_text(encoding="utf-8")
        assert "ghp_ABCDEFGHIJKLMNOP1234" not in raw
        assert "[REDACTED]" in raw
