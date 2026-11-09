"""Read-only MCP stdio surface — `api-sentinel mcp serve` (mcp extra).

Four tools, all read-only and honest: the Sentinel's real state over the
resolved workspace — service inventory, the capability surface (status +
proven stage), self health, and the incident feed. No mutations exist on
this surface by design; `forge.agentic.json` declares the command via
`mcp_command`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from api_sentinel import __version__
from api_sentinel.workspace import Workspace

_SERVER_NAME = "api-sentinel"


def _tool_payload(fn: Any, **kwargs: Any) -> list[Any]:
    """Run a read-only use case and wrap the JSON — errors surface as
    text, never as fabricated data."""
    import mcp.types as types

    try:
        data = fn(**kwargs)
    except Exception as exc:
        return [
            types.TextContent(
                type="text",
                text=json.dumps(
                    {"error": f"{type(exc).__name__}: {exc}"},
                    sort_keys=True,
                ),
            )
        ]
    return [
        types.TextContent(
            type="text",
            text=json.dumps(data, default=str, sort_keys=True),
        )
    ]


def _capabilities_payload() -> list[dict[str, Any]]:
    from api_sentinel.capabilities import list_capabilities

    return [
        {
            "capability_id": c.capability_id,
            "status": c.status.value,
            "stage": c.stage.value,
            "since": c.since,
            "notes": c.notes,
        }
        for c in list_capabilities()
    ]


def _health_payload(ws: Workspace) -> dict[str, Any]:
    from api_sentinel.operations.health import sentinel_health

    db_present = ws.db_path.is_file()
    sentinel: dict[str, Any] = {}
    if db_present:
        try:
            sentinel = sentinel_health(ws)
        except Exception:
            sentinel = {"state": "unavailable"}
    return {
        "state": "AVAILABLE" if db_present else "PARTIAL",
        "version": __version__,
        "workspace_initialized": ws.root.is_dir(),
        "database": "present" if db_present else "absent",
        "sentinel": sentinel,
        "unknowns": [] if db_present else ["database_absent"],
    }


def _services_payload(ws: Workspace) -> dict[str, Any]:
    from api_sentinel.application.use_cases import ListServices
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    services = ListServices(SqliteUnitOfWork(ws.db_path)).execute()
    return {
        "services": [
            s.model_dump(mode="json") if hasattr(s, "model_dump") else s
            for s in services
        ]
    }


def _incidents_payload(ws: Workspace, *, open_only: bool = False) -> dict[str, Any]:
    from api_sentinel.application.use_cases import ListIncidents
    from api_sentinel.infrastructure.storage import SqliteUnitOfWork

    with SqliteUnitOfWork(ws.db_path) as uow:
        incidents = ListIncidents(uow).execute(open_only=open_only)
    return {
        "incidents": [
            i.model_dump(mode="json") if hasattr(i, "model_dump") else i
            for i in incidents
        ]
    }


def _tools() -> list[Any]:
    import mcp.types as types

    return [
        types.Tool(
            name="sentinel_list_services",
            description=(
                "List every registered API service with coverage and "
                "honest health — real workspace inventory only."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="sentinel_capabilities",
            description=(
                "The declared capability surface — `status` "
                "(available/declared/unavailable) plus `stage` "
                "(highest proven maturity), never presence alone."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="sentinel_health",
            description=(
                "The Sentinel's own operational state — AVAILABLE or "
                "PARTIAL with named unknowns, never a fabricated score."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        types.Tool(
            name="sentinel_list_incidents",
            description=(
                "Persisted incidents; `open_only` filters to unresolved. "
                "Read-only — investigation is a governed mutation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "open_only": {
                        "type": "boolean",
                        "description": "return only unresolved incidents",
                        "default": False,
                    }
                },
                "additionalProperties": False,
            },
        ),
    ]


def tool_handlers(ws: Workspace) -> tuple[Any, Any]:
    """The (list_tools, call_tool) pair over `ws` — exported so tests can
    exercise the surface without a live stdio session."""
    import mcp.types as types

    tools = _tools()

    async def _list(
        _ctx: Any, _params: Any
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def _call(
        _ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        args = params.arguments or {}
        name = params.name
        if name == "sentinel_list_services":
            content = _tool_payload(_services_payload, ws=ws)
        elif name == "sentinel_capabilities":
            content = _tool_payload(_capabilities_payload)
        elif name == "sentinel_health":
            content = _tool_payload(_health_payload, ws=ws)
        elif name == "sentinel_list_incidents":
            content = _tool_payload(
                _incidents_payload,
                ws=ws,
                open_only=bool(args.get("open_only", False)),
            )
        else:
            content = [
                types.TextContent(
                    type="text",
                    text=json.dumps(
                        {"error": f"unknown tool: {name!r}"}, sort_keys=True
                    ),
                )
            ]
        return types.CallToolResult(content=content, isError=False)

    return _list, _call


def build_server(ws: Workspace) -> Any:
    """Build the low-level MCP `Server` over this workspace."""
    from mcp.server.lowlevel import Server

    list_tools, call_tool = tool_handlers(ws)
    return Server(
        _SERVER_NAME,
        version=__version__,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def serve_stdio(ws: Workspace) -> None:
    """Run the read-only MCP server over stdio — blocks until EOF."""
    import mcp.types as types  # noqa: F401 — proves the extra resolved
    from mcp.server.stdio import stdio_server

    server = build_server(ws)

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(
                read,
                write,
                server.create_initialization_options(),
            )

    asyncio.run(_run())
