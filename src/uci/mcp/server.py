"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0).

No third-party MCP SDK is required for local-lite. The request handler is separated from the stdio
loop so it can be unit-tested directly. An official-SDK transport can be added later without changing
tool logic.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..engine import Engine
from .tools import dispatch, list_tools

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC request. Returns a response dict, or ``None`` for notifications."""
        method = request.get("method")
        req_id = request.get("id")
        params = request.get("params") or {}

        try:
            if method == "initialize":
                result: Any = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": "unified-code-intelligence", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": list_tools(self.engine)}
            elif method == "tools/call":
                name = params.get("name", "")
                arguments = params.get("arguments") or {}
                data = dispatch(self.engine, name, arguments)
                result = {
                    "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                    "isError": not data.get("ok", True),
                }
            elif method == "ping":
                result = {}
            elif method in ("notifications/initialized", "notifications/cancelled"):
                return None  # notification: no response
            elif method == "shutdown":
                result = {}
            else:
                return _error(req_id, -32601, f"method not found: {method}")
        except Exception as exc:  # keep the server alive on tool errors
            return _error(req_id, -32603, f"internal error: {exc}")

        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def serve(self, stdin=None, stdout=None) -> None:  # pragma: no cover - I/O loop
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _write(stdout, _error(None, -32700, "parse error"))
                continue
            response = self.handle_request(request)
            if response is not None:
                _write(stdout, response)


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _write(stdout, payload: dict[str, Any]) -> None:  # pragma: no cover - I/O
    stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stdout.flush()


def serve_stdio(engine: Engine) -> None:  # pragma: no cover - I/O loop
    MCPServer(engine).serve()


__all__ = ["MCPServer", "serve_stdio", "PROTOCOL_VERSION"]
