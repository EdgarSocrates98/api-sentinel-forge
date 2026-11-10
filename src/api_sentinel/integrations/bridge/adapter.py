"""Bridge session adapters — HTTP transport + file seam + named
unavailable (ADR-010).

The Sentinel owns no agent runtime: `open_session` hands the governed
`AgentSessionRequest` to the Forge Agent Bridge — the Bridge owns
providers, approvals, budgets, storage and execution.

- ``HttpAgentSession`` — the real governed transport (C101-F004):
  `POST /agent-sessions` against the Bridge's loopback-only local
  service with bearer auth, origin binding and a replay nonce. The
  Sentinel never creates a provider session itself — it submits the
  governed request and records the Bridge-issued session reference.
- ``FileAgentSession`` — writes `session_request.json` (redacted) under
  `<workspace>/agent_sessions/<id>/` and reports ``delegated``: the same
  honest transport Doctor's handoff_file mode uses.
- ``UnavailableAgentSession`` — the named refusal when no transport
  exists; the result preserves the full governance block (principal,
  workspace, provider, cost limit, approvals) so the refusal audits
  exactly what was requested.
"""

from __future__ import annotations

import importlib.util
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from api_sentinel.infrastructure.security.paths import safe_slot
from api_sentinel.infrastructure.security.redact import redact
from api_sentinel.investigation.agent_session import (
    STATUS_DELEGATED,
    STATUS_FAILED,
    STATUS_REFUSED,
    STATUS_UNAVAILABLE,
    AgentSessionRequest,
    AgentSessionResult,
)

BRIDGE_FORGE = "forge-agent-bridge"
BRIDGE_MODULE = "forge_agent_bridge"


def bridge_importable() -> bool:
    """True iff `forge_agent_bridge` resolves — never import-for-side-effect."""
    return importlib.util.find_spec(BRIDGE_MODULE) is not None


class FileAgentSession:
    """Emit the governed request envelope under `agent_sessions/`.

    `delegated` means *the envelope is on disk for a separately-run
    Bridge* — never that a session executed. Replays dedup by the
    deterministic `session_request_id`: an existing file is reported,
    not rewritten.
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir

    def open_session(
        self, request: AgentSessionRequest
    ) -> AgentSessionResult:
        try:
            slot = safe_slot(
                self._dir,
                request.session_request_id,
                name="session_request_id",
            )
            slot.mkdir(parents=True, exist_ok=True)
            path = slot / "session_request.json"
            replayed = path.is_file()
            if not replayed:
                raw = request.model_dump_json(indent=2)
                path.write_text(redact(raw).text, encoding="utf-8")
            return AgentSessionResult(
                status=STATUS_DELEGATED,
                session_ref=str(path),
                detail=(
                    "session request replayed — envelope already emitted"
                    if replayed
                    else "session request emitted; the Forge Agent Bridge "
                    "consumes session_request.json and governs execution, "
                    "providers, approvals and cost"
                ),
                session_request_id=request.session_request_id,
                governance=request.governance,
            )
        except OSError as exc:
            return AgentSessionResult(
                status=STATUS_FAILED,
                detail=f"session request could not be emitted: {exc}",
                session_request_id=request.session_request_id,
                governance=request.governance,
            )


class UnavailableAgentSession:
    """The named refusal — Bridge absent, governance fully preserved."""

    def __init__(self, reason: str | None = None) -> None:
        self._reason = reason or (
            f"{BRIDGE_MODULE} not importable and no agent_sessions "
            "transport configured — agent sessions are Bridge-governed "
            "(ADR-010); an absent Bridge is a named state, never a "
            "simulated session"
        )

    def open_session(
        self, request: AgentSessionRequest
    ) -> AgentSessionResult:
        return AgentSessionResult(
            status=STATUS_UNAVAILABLE,
            detail=self._reason,
            session_request_id=request.session_request_id,
            governance=request.governance,
            unknowns=["agent_session_transport_absent"],
        )


class HttpAgentSession:
    """Real governed transport — the Bridge's loopback HTTP service.

    `delegated` here means *the Bridge accepted the governed request and
    issued a session reference* — provider selection, context binding,
    MCP filtering, budgets and execution stay Bridge-owned. Every
    non-2xx is a named state: auth/validation → ``refused``, transport
    unreachable → ``unavailable``, timeout/5xx → ``failed``.
    """

    def __init__(
        self,
        base_url: str,
        bearer: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or host not in (
            "127.0.0.1", "::1", "localhost",
        ):
            raise ValueError(
                "bridge base_url must be loopback http(s) — the local "
                f"service binds loopback only; got {base_url!r}"
            )
        self._base = base_url.rstrip("/")
        self._bearer = bearer
        self._timeout = timeout_seconds

    def open_session(
        self, request: AgentSessionRequest
    ) -> AgentSessionResult:
        gov = request.governance
        body = {
            "provider": gov.provider or "fake",
            "workspace_id": gov.workspace,
            "model": gov.model,
            "title": (
                f"sentinel:{request.incident_id}:"
                f"{request.session_request_id[:16]}"
            ),
            "context": {
                "schema": "forge/ProductAgentSessionContext/v2",
                "workspace_id": gov.workspace,
                "agent_profile": "sentinel-investigation",
                "permissions": list(gov.approvals),
                "allowed_tools": [],
            },
            "sentinel": {
                "session_request_id": request.session_request_id,
                "incident_id": request.incident_id,
                "pack_id": request.pack_id,
                "instructions": request.instructions.model_dump(mode="json"),
                "context_pack": request.context_pack.model_dump(mode="json"),
            },
        }
        try:
            payload = json.dumps(body).encode("utf-8")
        except (TypeError, ValueError) as exc:
            return AgentSessionResult(
                status=STATUS_FAILED,
                detail=f"session request not serializable: {exc}",
                session_request_id=request.session_request_id,
                governance=gov,
            )
        req = urllib.request.Request(  # noqa: S310 — loopback-only, validated
            f"{self._base}/agent-sessions",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._bearer}",
                "Content-Type": "application/json",
                "Origin": "http://127.0.0.1",
                "X-FAB-Nonce": uuid.uuid4().hex,
            },
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 — loopback-only, validated in __init__
            req, timeout=self._timeout
        ) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = self._http_detail(exc)
            status = (
                STATUS_REFUSED if exc.code in (400, 401, 403, 409, 413, 422, 429)
                else STATUS_FAILED
            )
            return AgentSessionResult(
                status=status,
                detail=detail,
                session_request_id=request.session_request_id,
                governance=gov,
            )
        except TimeoutError:
            return AgentSessionResult(
                status=STATUS_FAILED,
                detail=f"transport_timeout: bridge did not answer in "
                       f"{self._timeout}s — session creation state unknown",
                session_request_id=request.session_request_id,
                governance=gov,
            )
        except (urllib.error.URLError, OSError) as exc:
            return AgentSessionResult(
                status=STATUS_UNAVAILABLE,
                detail=f"bridge unreachable at {self._base}: {exc}",
                session_request_id=request.session_request_id,
                governance=gov,
                unknowns=["bridge_transport_unreachable"],
            )
        session_id = out.get("id") or out.get("session_id")
        if not session_id:
            return AgentSessionResult(
                status=STATUS_FAILED,
                detail="invalid_response: bridge 201 without a session id",
                session_request_id=request.session_request_id,
                governance=gov,
            )
        denied = out.get("mcp_denied") or []
        return AgentSessionResult(
            status=STATUS_DELEGATED,
            session_ref=str(session_id),
            detail=(
                f"session {session_id} created by Forge Agent Bridge "
                f"(provider {out.get('provider') or body['provider']}); "
                "execution, providers, approvals, budgets and event "
                "storage remain Bridge-governed"
                + (f"; mcp_denied={denied}" if denied else "")
            ),
            session_request_id=request.session_request_id,
            governance=gov,
        )

    @staticmethod
    def _http_detail(exc: urllib.error.HTTPError) -> str:
        """Named refusal detail from the Bridge's typed error envelope."""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            err = body.get("error") or {}
            code = err.get("code") or f"HTTP-{exc.code}"
            msg = err.get("message") or exc.reason
        except (ValueError, OSError):
            code, msg = f"HTTP-{exc.code}", str(exc.reason)
        return f"{code}: {str(msg)[:300]}"


def _pair_exchange(base_url: str, pair_token: str, timeout: float) -> str | None:
    """Master pair token → scoped session token (Bridge-local exchange)."""
    req = urllib.request.Request(  # noqa: S310 — loopback-only by contract
        f"{base_url.rstrip('/')}/pair/exchange",
        data=json.dumps({
            "pair_token": pair_token,
            "device_name": "api-sentinel",
            "scopes": ["session:manage", "session:read"],
        }).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Origin": "http://127.0.0.1"},
    )
    try:
        with urllib.request.urlopen(  # noqa: S310 — loopback-only, validated by caller
            req, timeout=timeout
        ) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    token = out.get("session_token")
    return str(token) if token else None


def _resolve_bearer(config: dict[str, Any], base_url: str,
                    timeout: float) -> str | None:
    """Bearer resolution: explicit config/env, else pair-token exchange."""
    explicit = config.get("bearer") or os.environ.get("FAB_BEARER")
    if explicit:
        return str(explicit)
    pair_path = Path(
        config.get("pair_token_path")
        or (Path.home() / ".forge-bridge" / "pair.token")
    )
    if pair_path.is_file():
        master = pair_path.read_text(encoding="utf-8").strip()
        if master:
            return _pair_exchange(base_url, master, timeout)
    return None


def bridge_session_from_config(
    config: dict[str, object],
    workspace_root: Path | None,
) -> Any:
    """Resolve the `AgentSessionPort` — http, file, or named unavailable.

    `integrations.bridge.mode` selects the transport:
    - ``http``: the real governed Bridge service — `base_url` +
      `bearer`/`FAB_BEARER`/`pair_token_path` (+pair exchange);
    - ``file`` (default when a workspace exists): emit envelopes under
      `<workspace>/agent_sessions/`;
    - ``unavailable``: explicit named refusal (e.g. CI surfaces that
      must never emit session envelopes).
    Absent workspace + file mode resolves the unavailable adapter —
    never a silent no-op; http mode without a resolvable bearer is the
    same named state, never an unauthenticated attempt.
    """
    integrations = config.get("integrations") if isinstance(config, dict) else None
    bridge = (
        integrations.get("bridge") if isinstance(integrations, dict) else None
    )
    cfg: dict[str, Any] = bridge if isinstance(bridge, dict) else {}
    mode = str(cfg.get("mode", "file"))
    if mode == "unavailable":
        return UnavailableAgentSession()
    if mode == "http":
        base_url = str(cfg.get("base_url", "http://127.0.0.1:8471"))
        timeout = float(cfg.get("timeout_seconds", 30.0))
        bearer = _resolve_bearer(cfg, base_url, timeout)
        if bearer is None:
            return UnavailableAgentSession(
                "bridge http mode configured but no bearer resolves — "
                "set FAB_BEARER, integrations.bridge.bearer, or run "
                "`forge-agent-bridge serve` and pair a device"
            )
        return HttpAgentSession(base_url, bearer, timeout_seconds=timeout)
    if workspace_root is not None:
        return FileAgentSession(workspace_root / "agent_sessions")
    return UnavailableAgentSession(
        "no workspace agent_sessions directory available — session "
        "requests have no transport to reach the Forge Agent Bridge"
    )
