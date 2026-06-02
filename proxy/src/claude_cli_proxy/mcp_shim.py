"""Standalone MCP stdio server that exposes a fixed set of fake tools.

The proxy needs claude to know about Hermes's tool inventory (terminal,
read_file, write_file, web_search, etc.) so the model emits `tool_use`
blocks for them. claude only loads tools from MCP servers (or its own
built-ins), so we register this shim via `--mcp-config` and pass the tool
schemas in via an env var.

When claude actually invokes one of these tools (mid-turn, before we can
kill the process), the shim returns an error sentinel. The proxy's caller
is expected to use `--max-turns 1` to ensure claude exits after the first
tool_use anyway, so the shim's error path is a defensive last resort.

Usage:
    HERMES_PROXY_TOOLS='[{...}, {...}]' python -m claude_cli_proxy.mcp_shim

The env var is JSON-encoded list of {name, description, input_schema} objects
matching the Anthropic tool-definition shape.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:  # pragma: no cover
    print("mcp package not installed; run: pip install mcp", file=sys.stderr)
    raise


def _load_tools() -> list[Tool]:
    raw = os.environ.get("HERMES_PROXY_TOOLS", "[]")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[Tool] = []
    for t in parsed:
        if not isinstance(t, dict):
            continue
        name = t.get("name")
        if not name:
            continue
        out.append(
            Tool(
                name=name,
                description=t.get("description", ""),
                inputSchema=t.get("input_schema") or {"type": "object", "properties": {}},
            )
        )
    return out


async def main() -> None:
    server: Server = Server("hermes_tools")
    tools = _load_tools()

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        # The proxy runs claude with --max-turns 1 so this branch should
        # rarely fire. If it does, return an error so the model gives up
        # rather than stalling.
        return [
            TextContent(
                type="text",
                text=(
                    f"Tool {name!r} is owned by the host process and cannot "
                    "be executed inside claude. Emit your tool_use block; "
                    "the host will run it and return the result."
                ),
            )
        ]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
