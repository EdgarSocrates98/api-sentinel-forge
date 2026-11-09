"""The Forge adapter — Sentinel as a typed specialist provider.

`SentinelBoundary` mirrors the Doctor seam shape (`describe` / `health` /
`capabilities` / `execute`) over `forge/v1` envelopes, translating requests
into local use cases — it is a boundary, never a router. `handoff`
translates `api-sentinel/InvestigationHandoff/v1` into a `forge/v1`
Request envelope and the Response back — version-strict in both directions.
"""

from api_sentinel.integrations.the_forge.boundary import SentinelBoundary
from api_sentinel.integrations.the_forge.handoff import (
    handoff_to_forge_request,
    response_to_result,
)

__all__ = [
    "SentinelBoundary",
    "handoff_to_forge_request",
    "response_to_result",
]
