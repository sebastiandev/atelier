"""Standalone Atelier MCP server (subprocess entry point).

Used by adapters whose SDK speaks the wire MCP protocol over stdio
rather than embedding tools in-process — currently the Amp adapter.
The Claude adapter uses ``create_sdk_mcp_server`` directly and doesn't
need this file.

Both paths surface the same three artifact-recording tools defined in
``atelier_mcp_tools``. The tool body is a trivial acknowledgement; the
actual recording happens in Atelier's supervisor when the adapter emits
an ``ArtifactMarker`` event in response to the tool use.

Run with::

    python -m src.infrastructure.agents.atelier_mcp_server
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.infrastructure.agents.atelier_mcp_tools import (
    MCP_SERVER_NAME,
    TOOL_DESCRIPTIONS,
    TOOL_RECORD_DOC,
    TOOL_RECORD_JIRA,
    TOOL_RECORD_PR,
    TOOL_SCHEMAS,
)

_TOOL_NAMES = (TOOL_RECORD_PR, TOOL_RECORD_JIRA, TOOL_RECORD_DOC)


def _build_server() -> Server[Any]:
    server: Server[Any] = Server(MCP_SERVER_NAME)

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=name,
                description=TOOL_DESCRIPTIONS[name],
                inputSchema=TOOL_SCHEMAS[name],
            )
            for name in _TOOL_NAMES
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(
        name: str, _arguments: dict[str, Any]
    ) -> list[TextContent]:
        if name not in _TOOL_NAMES:
            raise ValueError(f"unknown tool: {name}")
        return [
            TextContent(type="text", text="Artifact will be recorded by Atelier.")
        ]

    return server


async def _serve() -> None:
    server = _build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def main() -> None:
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
