"""`api-sentinel mcp serve` — read-only stdio tool surface (C10-F012).

The manifest's `mcp_command` declaration is now honest: the server lists
exactly the four declared read-only tools, and each returns the real
workspace state — never a fabricated payload.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from api_sentinel.mcp_server import tool_handlers
from api_sentinel.workspace import Workspace, ensure_workspace


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    return ensure_workspace(Workspace(root=tmp_path / "ws", scope="project"))


def _tool_names(ws: Workspace) -> list[str]:
    list_tools, _ = tool_handlers(ws)
    result = asyncio.run(list_tools(None, None))
    return [t.name for t in result.tools]


def _call(ws: Workspace, name: str, arguments: dict | None = None) -> object:
    import mcp.types as types

    _, call_tool = tool_handlers(ws)
    params = types.CallToolRequestParams(name=name, arguments=arguments or {})
    return asyncio.run(call_tool(None, params))


class TestToolSurface:
    def test_lists_exactly_the_four_declared_tools(self, ws: Workspace) -> None:
        assert set(_tool_names(ws)) == {
            "sentinel_list_services",
            "sentinel_capabilities",
            "sentinel_health",
            "sentinel_list_incidents",
        }

    def test_capabilities_carries_stage_and_status(self, ws: Workspace) -> None:
        result = _call(ws, "sentinel_capabilities")
        data = json.loads(result.content[0].text)  # type: ignore[attr-defined]
        assert data
        for cap in data:
            assert cap["status"] in ("available", "declared", "unavailable")
            assert cap["stage"] in (
                "implemented",
                "tested",
                "integrated",
                "verified",
                "production_validated",
            )

    def test_health_is_honest(self, ws: Workspace) -> None:
        result = _call(ws, "sentinel_health")
        data = json.loads(result.content[0].text)  # type: ignore[attr-defined]
        assert data["state"] in ("AVAILABLE", "PARTIAL")
        assert data["database"] in ("present", "absent")

    def test_unknown_tool_is_a_typed_error_not_a_crash(self, ws: Workspace) -> None:
        result = _call(ws, "sentinel_drop_table")
        data = json.loads(result.content[0].text)  # type: ignore[attr-defined]
        assert "error" in data
