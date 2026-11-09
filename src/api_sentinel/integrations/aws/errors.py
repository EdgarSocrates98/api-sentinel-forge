"""AWS adapter named refusals — every failure is a named terminal state.

Built on the shared integration errors (docs/06 degradation contract):
an absent credential chain, a refused call and an exhausted budget are
all *named* conditions — the adapter never fabricates a collection.
"""

from __future__ import annotations

from typing import Any

from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

AWS_FORGE = "aws"


def aws_unavailable(
    reason: str, *, details: dict[str, Any] | None = None
) -> IntegrationUnavailable:
    """No usable AWS transport/credential chain exists — named absence."""
    return IntegrationUnavailable(AWS_FORGE, reason, details=details)


def aws_refused(reason: str, *, details: dict[str, Any] | None = None) -> IntegrationRefused:
    """The adapter rejected a call before transport — named refusal."""
    return IntegrationRefused(AWS_FORGE, reason, details=details)


class AwsBudgetExceeded(IntegrationRefused):
    """A collection budget was exhausted before any work completed.

    Raised only when nothing could be collected: mid-collection budget
    hits are recorded as PARTIAL + unknowns instead (docs/14 — the
    cycle halts rather than degrading silently).
    """

    code = "sentinel.aws_budget_exceeded"

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(AWS_FORGE, reason, details=details)


class AwsThrottled(IntegrationRefused):
    """Throttling persisted past the configured retry budget — terminal."""

    code = "sentinel.aws_throttled"

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(AWS_FORGE, reason, details=details)
