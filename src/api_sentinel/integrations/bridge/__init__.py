"""Forge Agent Bridge integration — the governed agent-session seam."""

from api_sentinel.integrations.bridge.adapter import (
    BRIDGE_FORGE,
    BRIDGE_MODULE,
    FileAgentSession,
    UnavailableAgentSession,
    bridge_importable,
    bridge_session_from_config,
)

__all__ = [
    "BRIDGE_FORGE",
    "BRIDGE_MODULE",
    "FileAgentSession",
    "UnavailableAgentSession",
    "bridge_importable",
    "bridge_session_from_config",
]
