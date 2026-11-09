"""Named refusals for cross-Forge integrations (degradation contract, docs/06).

Every integration is optional: absence is a named state — never a simulated
execution or fabricated success. `IntegrationUnavailable` carries the target
forge and the human reason; `IntegrationRefused` carries a rejected
envelope/request (unknown version, missing scope).
"""

from __future__ import annotations

from typing import Any

from api_sentinel.domain.errors import SentinelError


class IntegrationUnavailable(SentinelError):
    """The target Forge cannot be reached through any real transport."""

    code = "sentinel.integration_unavailable"

    def __init__(
        self,
        forge: str,
        reason: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.forge = forge
        self.reason = reason
        super().__init__(
            f"integration unavailable: {forge} — {reason}",
            details={"forge": forge, "reason": reason, **(details or {})},
        )


class IntegrationRefused(SentinelError):
    """A delegation the adapter rejected before transport — named, explicit."""

    code = "sentinel.integration_refused"

    def __init__(
        self,
        forge: str,
        reason: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.forge = forge
        self.reason = reason
        super().__init__(
            f"integration refused: {forge} — {reason}",
            details={"forge": forge, "reason": reason, **(details or {})},
        )
