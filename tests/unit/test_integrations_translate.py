"""Unit tests — Sentinel ↔ Doctor / The Forge translation contracts."""

from __future__ import annotations

import json

import pytest

from api_sentinel.contracts.v1 import InvestigationHandoffV1
from api_sentinel.domain import ContractError
from api_sentinel.integrations.doctor.translate import (
    DOCTOR_PROTOCOL_VERSION,
    handoff_to_doctor_request,
    ingest_doctor_bundle,
    ingest_doctor_bundle_json,
)
from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)
from api_sentinel.integrations.the_forge.handoff import (
    handoff_to_forge_request,
    response_to_result,
)


def _handoff(**over) -> InvestigationHandoffV1:
    base = {
        "schema": "api-sentinel/InvestigationHandoff/v1",
        "handoff_id": "hnd_test1",
        "from_forge": "api-sentinel-forge",
        "to_forge": "forge-doctor-api",
        "kind": "api-investigation",
        "payload": {
            "service_id": "svc_orders",
            "capabilities": ["api.diagnose"],
            "anomaly_ids": ["anm_1"],
        },
        "constraints": {"evidence_refs": ["ev_1", "ev_2"], "budget_seconds": 30.0},
    }
    base.update(over)
    return InvestigationHandoffV1.model_validate(base)


class TestHandoffToDoctorRequest:
    def test_v1_handoff_emits_forge_request_v2(self) -> None:
        req = handoff_to_doctor_request(_handoff(), target="platform/orders", clock="2026-01-01")
        assert req["protocol_version"] == DOCTOR_PROTOCOL_VERSION
        assert req["request_id"] == "hnd_test1"
        assert req["target"] == "platform/orders"
        assert req["capabilities"] == ["api.diagnose"]
        assert req["context_refs"] == ["ev_1", "ev_2"]
        assert req["clock"] == "2026-01-01"

    def test_capabilities_sorted_deterministically(self) -> None:
        h = _handoff()
        h.payload["capabilities"] = ["z.cap", "a.cap"]
        req = handoff_to_doctor_request(h, target="t")
        assert req["capabilities"] == ["a.cap", "z.cap"]

    def test_unknown_schema_rejected_never_defaulted(self) -> None:
        h = _handoff()
        object.__setattr__(h, "schema_", "api-sentinel/InvestigationHandoff/v9")
        with pytest.raises(ContractError):
            handoff_to_doctor_request(h, target="t")


class TestIngestDoctorBundle:
    def test_findings_and_unknowns_verbatim(self) -> None:
        bundle = {
            "handoff_version": 2,
            "handoff_id": "abc",
            "analysis_rev": "rev1",
            "service": "orders",
            "findings": [
                {"id": "OAS001", "title": "spec gap", "severity": "high"},
            ],
            "unknowns": [
                {"subject": "x", "missing": "contract", "resolution": "add spec"},
            ],
            "capabilities": [{"name": "rest.openapi", "status": "detected"}],
            "context_refs": ["doctor://ctx/1"],
        }
        ingest = ingest_doctor_bundle(bundle)
        assert ingest.findings == bundle["findings"]
        assert ingest.unknowns == bundle["unknowns"]
        assert ingest.capabilities == bundle["capabilities"]
        assert ingest.analysis_rev == "rev1"
        assert ingest.service == "orders"

    def test_unknown_handoff_version_rejected(self) -> None:
        with pytest.raises(ContractError):
            ingest_doctor_bundle({"handoff_version": 99})

    def test_bundle_without_version_marker_rejected(self) -> None:
        """C10-F022: an arbitrary object with a `findings` key is not a
        Doctor bundle — the handoff marker is required, never assumed."""
        with pytest.raises(ContractError, match="handoff_version"):
            ingest_doctor_bundle({"schema": "wrong/schema", "findings": [{"forged": True}]})
        with pytest.raises(ContractError, match="handoff_version"):
            ingest_doctor_bundle_json(json.dumps({"findings": []}))

    def test_malformed_rejected(self) -> None:
        with pytest.raises(ContractError):
            ingest_doctor_bundle_json("not json")


class TestHandoffToForgeRequest:
    def test_v1_handoff_becomes_forge_v1_request(self) -> None:
        h = _handoff()
        h.payload.update(
            {
                "capability": "api.runtime-observation",
                "action": "summarize",
                "task": {"id": "t1", "intent": "correlate"},
                "context": {"files": []},
            }
        )
        req = handoff_to_forge_request(h)
        assert req["protocol"] == "forge/v1"
        assert req["kind"] == "Request"
        assert req["op"] == "execute"
        assert req["request_id"] == "hnd_test1"
        assert req["payload"]["capability"] == "api.runtime-observation"
        assert req["payload"]["action"] == "summarize"
        assert req["payload"]["handoff"]["handoff_id"] == "hnd_test1"

    def test_unknown_schema_named_refusal(self) -> None:
        h = _handoff()
        object.__setattr__(h, "schema_", "api-sentinel/InvestigationHandoff/v9")
        with pytest.raises(IntegrationRefused):
            handoff_to_forge_request(h)

    def test_missing_capability_named_refusal(self) -> None:
        with pytest.raises(IntegrationRefused):
            handoff_to_forge_request(_handoff())


class TestResponseToResult:
    def _resp(self, status: str, **kw) -> dict:
        r = {
            "protocol": "forge/v1",
            "kind": "Response",
            "request_id": "hnd_test1",
            "status": status,
            "payload": {"schema": "theforge/ExecutionResult/v1"},
            "limitations": [],
            "unknowns": [],
        }
        r.update(kw)
        return r

    def test_ok_returns_payload(self) -> None:
        assert response_to_result(self._resp("ok"))["schema"] == ("theforge/ExecutionResult/v1")

    def test_partial_returns_payload(self) -> None:
        assert response_to_result(self._resp("partial"))["schema"] == (
            "theforge/ExecutionResult/v1"
        )

    def test_refused_named(self) -> None:
        with pytest.raises(IntegrationRefused):
            response_to_result(self._resp("refused", error={"detail": "denied"}))

    def test_error_unavailable(self) -> None:
        with pytest.raises(IntegrationUnavailable):
            response_to_result(self._resp("error", error={"detail": "boom"}))

    def test_wrong_protocol_named(self) -> None:
        with pytest.raises(IntegrationRefused):
            response_to_result(self._resp("ok", protocol="forge/v9"))

    def test_wrong_result_schema_contract_error(self) -> None:
        with pytest.raises(ContractError):
            response_to_result(self._resp("ok", payload={"schema": "theforge/Other/v1"}))
