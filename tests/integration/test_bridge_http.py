"""Cycle 10.1 — Bridge HTTP transport conformance (C101-F004).

Two tiers, honestly separated:

- Lab server: a stdlib stub implementing the Bridge's wire contract —
  every non-2xx maps to a named state, governance travels verbatim,
  nonces are sent, timeouts are named `failed`.
- Real service: when `forge-agent-bridge/.venv` exists, the suite boots
  the actual local Bridge on an ephemeral port, pairs a device over the
  real exchange and opens a session — BRIDGE-CONTRACT VERIFIED. Real
  provider execution is out of scope without credentials (BLOCKED).
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from api_sentinel.integrations.bridge.adapter import (
    HttpAgentSession,
    UnavailableAgentSession,
    bridge_session_from_config,
)
from api_sentinel.investigation.agent_session import (
    AgentSessionRequest,
    SessionGovernance,
)
from api_sentinel.investigation.context_pack import (
    ContextPack,
    PackInstructions,
)

BRIDGE_EXE = (
    Path(__file__).resolve().parents[3]
    / "forge-agent-bridge" / ".venv" / "Scripts" / "forge-bridge.exe"
)
BRIDGE_HAS_VENV = BRIDGE_EXE.is_file()


def _request(**over) -> AgentSessionRequest:
    gov = SessionGovernance(
        principal="sentinel-operator",
        workspace="ws_lab",
        provider="fake",
        approvals=["read-only"],
    )
    from datetime import UTC, datetime

    pack = ContextPack(
        pack_id="pack_1",
        incident_id="inc_1",
        built_at=datetime(2026, 1, 1, tzinfo=UTC),
        budget_bytes=65536,
        size_bytes=128,
        instructions=PackInstructions(text="investigate inc_1"),
    )
    fields = {
        "session_request_id": "asr_lab1",
        "incident_id": "inc_1",
        "pack_id": "pack_1",
        "instructions": pack.instructions,
        "context_pack": pack,
        "governance": gov,
    }
    fields.update(over)
    return AgentSessionRequest(**fields)


class _LabBridge(BaseHTTPRequestHandler):
    """Minimal contract-faithful stub of the Bridge local service."""

    token = "lab-bearer"  # noqa: S105 — lab stub token

    def log_message(self, *a: object) -> None:
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        if self.path == "/pair/exchange":
            if body.get("pair_token") == "lab-master":
                return self._send(201, {"session_token": "lab-bearer",
                                        "device": {"name": "sentinel"}})
            return self._send(403, {"error": {
                "code": "FAB-FORBIDDEN", "message": "invalid pair token"}})
        if self.path == "/agent-sessions":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return self._send(401, {"error": {
                    "code": "FAB-UNAUTHORIZED", "message": "bearer required"}})
            if auth[7:] != self.token:
                return self._send(403, {"error": {
                    "code": "FAB-FORBIDDEN", "message": "bad token"}})
            if self.headers.get("X-FAB-Nonce") in self.server.seen_nonces:
                return self._send(409, {"error": {
                    "code": "FAB-REPLAY", "message": "nonce replay"}})
            self.server.seen_nonces.add(self.headers.get("X-FAB-Nonce"))
            if not body.get("provider"):
                return self._send(422, {"error": {
                    "code": "FAB-PROVIDER-REQUIRED",
                    "message": "provider is required"}})
            self.server.bodies.append(body)
            return self._send(201, {"id": "ses_lab1", "provider":
                                    body["provider"],
                                    "workspace_id": body.get("workspace_id")})
        return self._send(404, {"error": {"code": "FAB-NOTFOUND",
                                        "message": "no route"}})


@pytest.fixture()
def lab_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _LabBridge)
    srv.seen_nonces = set()
    srv.bodies = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


class TestLabTransport:
    def test_delegated_on_201(self, lab_server: str) -> None:
        acl = HttpAgentSession(lab_server, "lab-bearer", timeout_seconds=5)
        r = acl.open_session(_request())
        assert r.status == "delegated"
        assert r.session_ref == "ses_lab1"
        assert "Forge Agent Bridge" in r.detail

    def test_governance_travels_verbatim(self, lab_server: str,
                                       monkeypatch) -> None:
        bodies: list[dict] = []
        original = _LabBridge.do_POST

        def capture(self):
            length = int(self.headers.get("Content-Length") or 0)
            bodies.append(json.loads(self.rfile.read(length) or b"{}"))
            # replay the body to the real handler via a fresh reader
            self.rfile = type("R", (), {"read": lambda s, n: json.dumps(bodies[-1]).encode()})()
            return original(self)

        monkeypatch.setattr(_LabBridge, "do_POST", capture)
        acl = HttpAgentSession(lab_server, "lab-bearer", timeout_seconds=5)
        acl.open_session(_request())
        sent = bodies[-1]
        assert sent["provider"] == "fake"
        assert sent["workspace_id"] == "ws_lab"
        assert sent["context"]["permissions"] == ["read-only"]
        assert sent["sentinel"]["session_request_id"] == "asr_lab1"
        assert sent["sentinel"]["incident_id"] == "inc_1"
        monkeypatch.setattr(_LabBridge, "do_POST", original)

    def test_unauthenticated_refused(self, lab_server: str) -> None:
        acl = HttpAgentSession(lab_server, "wrong-token", timeout_seconds=5)
        r = acl.open_session(_request())
        assert r.status == "refused"
        assert "FAB-FORBIDDEN" in r.detail

    def test_replay_refused(self, lab_server: str) -> None:
        acl = HttpAgentSession(lab_server, "lab-bearer", timeout_seconds=5)
        # force the same nonce twice — a replayed request is refused
        import api_sentinel.integrations.bridge.adapter as mod
        original_uuid = mod.uuid.uuid4
        mod.uuid.uuid4 = lambda: type("U", (), {"hex": "same-nonce"})()
        try:
            first = acl.open_session(_request())
            second = acl.open_session(_request())
        finally:
            mod.uuid.uuid4 = original_uuid
        assert first.status == "delegated"
        assert second.status == "refused"
        assert "FAB-REPLAY" in second.detail

    def test_unreachable_named_unavailable(self) -> None:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        dead_port = sock.getsockname()[1]
        sock.close()  # nothing listens
        acl = HttpAgentSession(
            f"http://127.0.0.1:{dead_port}", "x", timeout_seconds=2
        )
        r = acl.open_session(_request())
        assert r.status == "unavailable"
        assert "bridge_transport_unreachable" in r.unknowns

    def test_pair_exchange_flow(self, lab_server: str,
                                monkeypatch, tmp_path: Path) -> None:
        pair = tmp_path / "pair.token"
        pair.write_text("lab-master")
        cfg = {
            "integrations": {"bridge": {
                "mode": "http",
                "base_url": lab_server,
                "pair_token_path": str(pair),
                "timeout_seconds": 5,
            }}
        }
        port = bridge_session_from_config(cfg, None)
        assert isinstance(port, HttpAgentSession)
        r = port.open_session(_request())
        assert r.status == "delegated"

    def test_http_without_bearer_is_named_unavailable(
        self, lab_server: str, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("FAB_BEARER", raising=False)
        cfg = {"integrations": {"bridge": {
            "mode": "http", "base_url": lab_server,
            "pair_token_path": str(tmp_path / "absent.token"),
        }}}
        port = bridge_session_from_config(cfg, None)
        assert isinstance(port, UnavailableAgentSession)
        r = port.open_session(_request())
        assert r.status == "unavailable"


@pytest.mark.skipif(
    not BRIDGE_HAS_VENV,
    reason="forge-agent-bridge/.venv absent — contract tier only",
)
class TestRealBridgeService:
    """BRIDGE-CONTRACT VERIFIED — the real service, real pairing.

    Provider execution (a real claude/codex run) needs credentials this
    suite never fabricates; contract verification uses the Bridge's own
    `fake` provider — a deterministic double, honestly labeled.
    """

    @pytest.fixture()
    def real_bridge(self, tmp_path: Path):
        proc_env = dict(__import__("os").environ)
        proc_env["FAB_STATE_DIR"] = str(tmp_path / "fab-state")
        home = tmp_path / "home"
        home.mkdir()
        proc_env["USERPROFILE"] = str(home)
        proc_env["HOME"] = str(home)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, known exe
            [str(BRIDGE_EXE), "serve", "--port", str(port)],
            env=proc_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.time() + 20
            up = False
            while time.time() < deadline:
                try:
                    # any HTTP response — even the expected 401 without
                    # credentials — proves the service is listening
                    urllib.request.urlopen(  # noqa: S310 — lab loopback
                        base + "/status", timeout=1
                    )
                    up = True
                    break
                except urllib.error.HTTPError:
                    up = True
                    break
                except OSError:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.3)
            if not up:
                proc.kill()
                out = proc.stdout.read() if proc.stdout else ""
                pytest.skip(f"bridge service did not start: {out[-400:]}")
            yield base, home
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_pair_and_open_session_real(self, real_bridge) -> None:
        base, home = real_bridge
        pair_file = home / ".forge-bridge" / "pair.token"
        if not pair_file.is_file():
            # the bridge issues pair.token on first serve; poll briefly
            for _ in range(30):
                if pair_file.is_file():
                    break
                time.sleep(0.2)
        assert pair_file.is_file(), "pair.token not issued"
        cfg = {
            "integrations": {"bridge": {
                "mode": "http",
                "base_url": base,
                "pair_token_path": str(pair_file),
                "timeout_seconds": 10,
            }}
        }
        port = bridge_session_from_config(cfg, None)
        assert isinstance(port, HttpAgentSession)
        r = port.open_session(_request())
        assert r.status == "delegated", r.detail
        assert r.session_ref and r.session_ref.startswith("ses")

    def test_bad_bearer_refused_real(self, real_bridge) -> None:
        base, _ = real_bridge
        acl = HttpAgentSession(base, "totally-bogus", timeout_seconds=5)
        r = acl.open_session(_request())
        assert r.status == "refused"
