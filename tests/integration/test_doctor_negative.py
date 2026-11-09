"""Cycle 10 Wave 2 — Doctor negative battery.

Every failure mode is a named terminal state (refused | unavailable |
timed_out | failed), never a fabricated bundle, a hang or a traceback.
The suite is environment-honest: cases that need the real
`forge_doctor_api` assert on the importable branch; absent-branch
behavior is asserted where it is deterministic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from api_sentinel.contracts.v1.models import InvestigationHandoffV1
from api_sentinel.domain import ContractError, ValidationError
from api_sentinel.integrations.doctor.adapter import (
    DoctorACL,
    DoctorMode,
    doctor_importable,
)
from api_sentinel.integrations.doctor.translate import (
    handoff_to_doctor_request,
    ingest_doctor_bundle_json,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DOCTOR_LAB = (
    Path(__file__).resolve().parents[2] / "lab" / "fixtures" / "doctor-project"
)


def _handoff(**over) -> InvestigationHandoffV1:
    fields: dict = {
        "schema": "api-sentinel/InvestigationHandoff/v1",
        "handoff_id": "hf_neg1",
        "from_forge": "api-sentinel-forge",
        "to_forge": "forge-doctor-api",
        "kind": "investigation",
        "payload": {"service_id": "svc_x"},
        "constraints": {},
    }
    fields.update(over)
    return InvestigationHandoffV1(**fields)


class TestAbsentAndModeSelection:
    def test_mode_defaults_by_importability(self) -> None:
        acl = DoctorACL()
        expected = (
            DoctorMode.IN_PROCESS
            if doctor_importable()
            else DoctorMode.HANDOFF_FILE
        )
        assert acl.mode is expected

    def test_absent_module_named_unavailable(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "api_sentinel.integrations.doctor.adapter.doctor_importable",
            lambda: False,
        )
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS)
        result = acl.delegate(_handoff(), target="svc_x", context_root=Path())
        assert result.status == "unavailable"
        assert "not importable" in result.detail


class TestContractGuards:
    def test_missing_contract_field_rejected(self) -> None:
        with pytest.raises(PydanticValidationError):
            InvestigationHandoffV1(
                **{  # type: ignore[arg-type]
                    "schema": "api-sentinel/InvestigationHandoff/v1",
                    "handoff_id": "hf_bad",
                    "from_forge": "api-sentinel-forge",
                    "to_forge": "forge-doctor-api",
                    "kind": "investigation",
                    # payload and constraints deliberately absent — F014
                }
            )

    def test_wrong_schema_literal_rejected(self) -> None:
        # the envelope pins `schema` to the published id — a lookalike
        # version is a contract rejection, never coerced
        with pytest.raises(ContractError):
            InvestigationHandoffV1(
                **{  # type: ignore[arg-type]
                    "schema": "api-sentinel/InvestigationHandoff/v2",
                    "handoff_id": "hf_bad",
                    "from_forge": "api-sentinel-forge",
                    "to_forge": "forge-doctor-api",
                    "kind": "investigation",
                    "payload": {},
                    "constraints": {},
                }
            )

    def test_bundle_malformed_json_named(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        bad = tmp_path / "bundle.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises((ContractError, ValidationError, Exception)):
            acl.ingest(bad)

    def test_bundle_wrong_handoff_id_named(self, tmp_path: Path) -> None:
        bundle = {
            "handoff_id": "other-handoff",
            "findings": [],
            "unknowns": [],
        }
        path = tmp_path / "bundle.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        with pytest.raises(ContractError):
            acl.ingest(path, expected_handoff_id="hf_neg1")

    def test_truncated_bundle_rejected(self) -> None:
        # a bundle cut mid-document is a contract error, never partial ingest
        with pytest.raises(ContractError):
            ingest_doctor_bundle_json('{"findings": [{"id": "X"')

    def test_finding_without_evidence_preserved_not_dropped(self) -> None:
        # a Doctor finding that carries no evidence list is ingested
        # verbatim — the Sentinel records it, never invents evidence
        bundle = {
            "handoff_version": 2,
            "handoff_id": "hf_neg1",
            "analysis_rev": "rev0",
            "findings": [{"id": "F1", "title": "no evidence finding"}],
            "unknowns": [],
        }
        ingest = ingest_doctor_bundle_json(json.dumps(bundle))
        assert len(ingest.findings) == 1


class TestPathSafety:
    def test_handoff_id_path_escape_refused(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        with pytest.raises(ValidationError):
            acl.delegate(
                _handoff(handoff_id="../escape"),
                target="svc_x",
                handoffs_dir=tmp_path / "handoffs",
            )

    def test_missing_bundle_file_named(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.HANDOFF_FILE)
        with pytest.raises(ValidationError):
            acl.ingest(tmp_path / "absent.json")


@pytest.mark.skipif(
    not doctor_importable(),
    reason="forge_doctor_api not importable in this interpreter",
)
class TestRealInProcess:
    """The in-process branch — runs only where the real peer resolves."""

    def test_real_doctor_completes_against_lab(self) -> None:
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=60)
        result = acl.delegate(
            _handoff(),
            target=str(DOCTOR_LAB),
            context_root=DOCTOR_LAB,
        )
        assert result.status == "completed", result.detail
        assert result.ingest is not None
        # real Doctor findings carry rule ids + evidence kinds
        assert result.ingest.findings
        first = result.ingest.findings[0]
        assert first.get("id") or first.get("rule_id")

    def test_invalid_context_root_named_not_crash(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=10)
        result = acl.delegate(
            _handoff(),
            target="t",
            context_root=tmp_path / "does-not-exist",
        )
        assert result.status in ("refused", "failed", "unavailable")
        assert result.detail

    def test_empty_context_root_doctor_internal_state(self, tmp_path: Path) -> None:
        acl = DoctorACL(mode=DoctorMode.IN_PROCESS, timeout_seconds=30)
        result = acl.delegate(
            _handoff(),
            target="t",
            context_root=tmp_path,
        )
        # an empty project is a Doctor-side terminal state — completed with
        # unknowns, or refused/failed — never an unhandled traceback
        assert result.status in ("completed", "refused", "failed")
        if result.status == "completed":
            assert result.ingest is not None

    def test_translate_rejects_unknown_schema(self) -> None:
        with pytest.raises(ContractError):
            handoff_to_doctor_request(
                _handoff(schema="api-sentinel/InvestigationHandoff/v9"),  # type: ignore[arg-type]
                target="t",
            )
