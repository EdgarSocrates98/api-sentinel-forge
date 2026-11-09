"""Cross-Forge integrations — anti-corruption-layer adapters only.

The Sentinel never reimplements Doctor analysis, Atlas graphs or The Forge
routing. Every sibling Forge is consumed through a public versioned
contract; absence produces a named unavailable state, never a fabricated
result (docs/06 degradation contract).
"""

from api_sentinel.integrations.errors import (
    IntegrationRefused,
    IntegrationUnavailable,
)

__all__ = ["IntegrationRefused", "IntegrationUnavailable"]
