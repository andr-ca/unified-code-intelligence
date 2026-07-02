"""``uci.mcp`` — MCP server exposing agent tools over the canonical graph."""

from __future__ import annotations

from .server import MCPServer, serve_stdio
from .tools import TOOL_SPECS, dispatch, list_tools

__all__ = ["MCPServer", "serve_stdio", "TOOL_SPECS", "list_tools", "dispatch"]
