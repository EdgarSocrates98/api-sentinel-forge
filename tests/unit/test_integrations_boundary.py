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

    def test_declared_caps_heuristic_not_supported(self) -> None:
        m = SentinelBoundary().describe()
        states = {c["id"]: c["state"] for c in m["capabilities"]}
        # Cycle-7 capabilities are discovered but heuristic — not executable.
        assert states["api.aws-readonly-observability"] == "heuristic"
        assert states["api.portal-operations"] == "supported"
        assert states["api.runtime-observation"] == "supported"


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
        # Declared caps surface as unknowns — honest, not executable.
        assert any("api.aws-readonly-observability" in u for u in r["unknowns"])


class TestExecute:
    def test_unknown_capability_refused(self) -> None:
        resp = SentinelBoundary().handle(
            _req("execute", {"capability": "api.nope", "action": "run"})
        )
        assert resp["status"] == "refused"
        assert resp["error"]["code"] == "APISENT-UNKNOWN-CAPABILITY"

    def test_declared_capability_refused_not_simulated(self) -> None:
        # api.aws-readonly-observability is DECLARED — discovered but not executable.
        resp = SentinelBoundary().handle(
            _req(
                "execute",
                {"capability": "api.aws-readonly-observability", "action": "list"},
            )
        )
        assert resp["status"] == "refused"
        assert "UNAVAILABLE" in resp["error"]["code"]

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
