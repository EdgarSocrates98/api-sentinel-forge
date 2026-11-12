"""Unit tests — SentinelBoundary: forge/v1 seam, DISCOVERED ≠ VERIFIED."""

from __future__ import annotations

from api_sentinel.integrations.the_forge.boundary import (
    PROVIDER_ID,
    SentinelBoundary,
)


def _req(op: str, payload: dict | None = None, **over) -> dict:
    r = {
        "protocol": "forge/v1",
        "kind": "Request",
        "request_id": "r_test1",
        "op": op,
        "payload": payload or {},
    }
    r.update(over)
    return r


class TestDescribe:
    def test_manifest_shape_and_runtime_discovery(self) -> None:
        m = SentinelBoundary().describe()
        assert m["schema"] == "theforge/ForgeManifest/v1"
        assert m["id"] == PROVIDER_ID
        assert "forge/v1" in m["protocols"]
        assert set(m["ops"]) == {"describe", "health", "capabilities", "execute"}
        ids = {c["id"] for c in m["capabilities"]}
        for cap in (
            "api.runtime-observation",
            "api.slo-assessment",
            "api.anomaly-detection",
            "api.incident-investigation",
            "api.deployment-correlation",
        ):
            assert cap in ids

    def test_aws_cap_supported_only_when_readonly_proof_passes(self) -> None:
        from api_sentinel.integrations.aws.client import prove_read_only

        m = SentinelBoundary().describe()
        states = {c["id"]: c["state"] for c in m["capabilities"]}
        # Cycle 10 W2: the manifest declares only pairs with real executors
        # (theforge/ForgeManifest/v1 requires a default_action) — AWS and
        # portal have none, so they are absent here and visible via
        # `capabilities()` instead.
        _passed, _detail = prove_read_only()
        assert "api.aws-readonly-observability" not in states
        assert "api.portal-operations" not in states
        assert states["api.runtime-observation"] == "supported"
        caps = SentinelBoundary().capabilities()
        cap_states = {f["id"] for f in caps["findings"]}
        assert "api.aws-readonly-observability" in cap_states
        assert "api.portal-operations" in cap_states


class TestHealth:
    def test_health_shape(self) -> None:
        h = SentinelBoundary().health()
        assert h["status"] in ("ok", "degraded", "unavailable")
        assert isinstance(h["checks"], list)


class TestCapabilities:
    def test_result_shape(self) -> None:
        r = SentinelBoundary().capabilities()
        assert r["schema"] == "theforge/ExecutionResult/v1"
        assert r["status"] in ("ok", "partial")
        ids = {f["id"] for f in r["findings"]}
        assert "api.runtime-observation" in ids
        assert "api.aws-readonly-observability" in ids
        # Declared caps still surface as unknowns — honest, not executable.
        # AWS is DECLARED only while the read-only proof fails.
        from api_sentinel.integrations.aws.client import prove_read_only

        passed, _detail = prove_read_only()
        if not passed:
            assert any("api.aws-readonly-observability" in u for u in r["unknowns"])


class TestExecute:
    def test_unknown_capability_refused(self) -> None:
        resp = SentinelBoundary().handle(
            _req("execute", {"capability": "api.nope", "action": "run"})
        )
        assert resp["status"] == "refused"
        assert resp["error"]["code"] == "APISENT-UNKNOWN-CAPABILITY"

    def test_aws_capability_has_no_forge_routed_action(self) -> None:
        # Cycle 7: the capability is AVAILABLE (proof-backed), but the
        # forge seam routes no action for it — collection is CLI-scoped
        # (`api-sentinel aws collect`), so every action is a named refusal.
        from api_sentinel.integrations.aws.client import prove_read_only

        resp = SentinelBoundary().handle(
            _req(
                "execute",
                {"capability": "api.aws-readonly-observability", "action": "list"},
            )
        )
        assert resp["status"] == "refused"
        passed, _detail = prove_read_only()
        expected = "APISENT-UNKNOWN-ACTION" if passed else "APISENT-CAPABILITY-UNAVAILABLE"
        assert resp["error"]["code"] == expected

    def test_unknown_action_refused(self) -> None:
        resp = SentinelBoundary().handle(
            _req(
                "execute",
                {"capability": "api.runtime-observation", "action": "delete"},
            )
        )
        assert resp["status"] == "refused"
        assert resp["error"]["code"] == "APISENT-UNKNOWN-ACTION"


class TestHandleGate:
    def test_wrong_protocol_refused(self) -> None:
        resp = SentinelBoundary().handle(_req("describe", protocol="forge/v9"))
        assert resp["status"] == "refused"
        assert resp["error"]["code"] == "APISENT-PROTOCOL"

    def test_wrong_kind_refused(self) -> None:
        resp = SentinelBoundary().handle(_req("describe", kind="Response"))
        assert resp["status"] == "refused"

    def test_unknown_op_refused(self) -> None:
        resp = SentinelBoundary().handle(_req("teleport"))
        assert resp["status"] == "refused"
        assert resp["error"]["code"] == "APISENT-UNKNOWN-OP"

    def test_describe_round_trip(self) -> None:
        resp = SentinelBoundary().handle(_req("describe"))
        assert resp["status"] == "ok"
        assert resp["payload"]["schema"] == "theforge/ForgeManifest/v1"
        assert resp["producer"]["id"] == PROVIDER_ID
