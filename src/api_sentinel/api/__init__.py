"""Local HTTP surface — the thin /v1 operations API (docs/04, ADR-011).

This package is the designated *optional* network surface: the core engine
never imports it. Routers stay thin — they deserialize, check the scope and
call application use cases; no business logic lives here.
"""

from api_sentinel.api.app import create_app

__all__ = ["create_app"]
