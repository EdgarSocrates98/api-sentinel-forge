"""Bridge session adapters — file seam + named unavailable (ADR-010).

The Sentinel owns no agent runtime: `open_session` only *emits* the
governed `AgentSessionRequest` envelope for the Forge Agent Bridge to
consume — the Bridge owns providers, approvals, budgets and execution.

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
from pathlib import Path
from typing import Any

from api_sentinel.infrastructure.security.redact import redact
from api_sentinel.investigation.agent_session import (
    STATUS_DELEGATED,
    STATUS_FAILED,
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
            slot = self._dir / request.session_request_id
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


def bridge_session_from_config(
    config: dict[str, object],
    workspace_root: Path | None,
) -> Any:
    """Resolve the `AgentSessionPort` — file seam, or named unavailable.

    `integrations.bridge.mode` selects the transport:
    - ``file`` (default when a workspace exists): emit envelopes under
      `<workspace>/agent_sessions/`;
    - ``unavailable``: explicit named refusal (e.g. CI surfaces that
      must never emit session envelopes).
    Absent workspace + file mode resolves the unavailable adapter —
    never a silent no-op.
    """
    integrations = config.get("integrations") if isinstance(config, dict) else None
    bridge = (
        integrations.get("bridge") if isinstance(integrations, dict) else None
    )
    mode = str(bridge.get("mode", "file")) if isinstance(bridge, dict) else "file"
    if mode == "unavailable":
        return UnavailableAgentSession()
    if workspace_root is not None:
        return FileAgentSession(workspace_root / "agent_sessions")
    return UnavailableAgentSession(
        "no workspace agent_sessions directory available — session "
        "requests have no transport to reach the Forge Agent Bridge"
    )
