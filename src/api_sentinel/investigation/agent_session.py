"""The Bridge session port — governed agentic sessions (ADR-010, docs/06).

The Sentinel never builds an agent runtime: a session is *requested*
through this port and created/governed by the Forge Agent Bridge. The
request preserves every governance attribute the AC requires — principal,
workspace, provider choice, cost limit, approvals — and carries the
bounded `ContextPack` as the authorized context.

Prompt-injection boundary (docs/07): `build_session_request` lifts agent
instructions **only** from `pack.instructions` — the Sentinel-authored
section. `contents[]` travel as untrusted data, fenced by `role="data"`;
a data field reading "ignore previous instructions" stays an inert datum.

Adapters live in `integrations/bridge/adapter.py` (file seam + named
unavailable refusal) so this module stays transport-free.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from api_sentinel.domain.identity import FrozenModel
from api_sentinel.investigation.context_pack import ContextPack, PackInstructions

#: Session outcome vocabulary — a named state, never a hang, never faked.
STATUS_DELEGATED = "delegated"
STATUS_UNAVAILABLE = "unavailable"
STATUS_REFUSED = "refused"
STATUS_FAILED = "failed"


class SessionGovernance(FrozenModel):
    """The governance attributes a Bridge session must preserve verbatim."""

    principal: str  # authorized actor requesting the session
    workspace: str  # workspace scope the session is bound to
    provider: str | None = None  # provider choice (e.g. claude, codex)
    model: str | None = None
    cost_limit_usd: float | None = None
    approvals: list[str] = Field(default_factory=list)  # granted scopes only


class AgentSessionRequest(FrozenModel):
    """The governed session-request envelope handed to the Bridge port.

    `instructions` is the *only* instruction channel — copied verbatim
    from the pack's instructions section; `context_pack` is the bounded,
    redacted, `role="data"`-fenced evidence pack.
    """

    session_request_id: str
    incident_id: str
    pack_id: str
    instructions: PackInstructions
    context_pack: ContextPack
    governance: SessionGovernance


class AgentSessionResult(FrozenModel):
    """What an `open_session` attempt produced — honest, named, governed.

    `governance` is preserved even on refusal/unavailability: the caller
    can audit exactly which principal, workspace, provider, cost limit
    and approvals the request carried.
    """

    status: str  # delegated | unavailable | refused | failed
    session_ref: str | None = None  # envelope/session ref when delegated
    detail: str = ""
    session_request_id: str = ""
    governance: SessionGovernance
    unknowns: list[str] = Field(default_factory=list)


def session_request_id(*, pack_id: str, principal: str, workspace: str) -> str:
    """Deterministic request identity — a replayed call dedups by id."""
    canonical = f"{pack_id}|{principal}|{workspace}"
    return "asr_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def build_session_request(
    *,
    context_pack: ContextPack,
    principal: str,
    workspace: str,
    provider_choice: str | None = None,
    model: str | None = None,
    cost_limit: float | None = None,
    approvals: list[str] | tuple[str, ...] = (),
) -> AgentSessionRequest:
    """Bind governance + pack into the governed request envelope.

    Instructions come ONLY from `pack.instructions` — the caller cannot
    inject prose; the pack's data sections stay fenced `role="data"`.
    """
    governance = SessionGovernance(
        principal=principal,
        workspace=workspace,
        provider=provider_choice,
        model=model,
        cost_limit_usd=cost_limit,
        approvals=sorted(set(approvals)),
    )
    return AgentSessionRequest(
        session_request_id=session_request_id(
            pack_id=context_pack.pack_id,
            principal=principal,
            workspace=workspace,
        ),
        incident_id=context_pack.incident_id,
        pack_id=context_pack.pack_id,
        instructions=context_pack.instructions,
        context_pack=context_pack,
        governance=governance,
    )


@runtime_checkable
class AgentSessionPort(Protocol):
    """The governed seam to the Agent Bridge (ADR-010).

    `open_session` returns a named `AgentSessionResult` — `delegated` when
    a real transport emitted the request, `unavailable` when no Bridge
    seam exists (never a simulated session), `refused`/`failed` for named
    rejections. The Bridge owns execution, providers, approvals and cost.
    """

    def open_session(self, request: AgentSessionRequest) -> AgentSessionResult: ...


def session_result_summary(result: AgentSessionResult) -> dict[str, Any]:
    """Compact audit view — governance attributes always present."""
    return {
        "status": result.status,
        "session_ref": result.session_ref,
        "detail": result.detail,
        "principal": result.governance.principal,
        "workspace": result.governance.workspace,
        "provider": result.governance.provider,
        "cost_limit_usd": result.governance.cost_limit_usd,
        "approvals": ",".join(result.governance.approvals),
    }
