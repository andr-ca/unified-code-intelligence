"""API/dashboard tests: run the stdlib HTTP server on an ephemeral port and hit real endpoints."""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from http.server import HTTPServer

import pytest

from uci.api.server import make_handler


@pytest.fixture
def server(indexed_engine):
    httpd = HTTPServer(("127.0.0.1", 0), make_handler(indexed_engine))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, indexed_engine
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def test_api_overview_json(server):
    base, _ = server
    raw, ctype = _get(base, "/api/overview")
    data = json.loads(raw)
    assert "application/json" in ctype
    assert data["totals"]["classes"] >= 3


def test_dashboard_html(server):
    base, _ = server
    raw, ctype = _get(base, "/")
    assert "text/html" in ctype
    assert b"Unified Code Intelligence" in raw and b"overview" in raw.lower()


def test_api_graph_neighborhood(server):
    base, engine = server
    root, _ = engine.default_graph_root()
    raw, _ = _get(base, "/api/graph?id=" + urllib.parse.quote(root) + "&depth=2")
    data = json.loads(raw)
    assert data["ok"] and data["nodes"]


def test_api_impact(server):
    base, _ = server
    raw, _ = _get(base, "/api/impact?q=" + urllib.parse.quote("PricingCalculator.calculate"))
    data = json.loads(raw)
    assert data["ok"] and data["callers"]["resolved"]


def test_api_mcp_tools(server):
    base, _ = server
    from uci.mcp.tools import TOOL_SPECS
    raw, _ = _get(base, "/api/mcp/tools")
    tools = json.loads(raw)["tools"]
    names = {t["name"] for t in tools}
    assert "search_code" in names and "list_index_gaps" in names
    assert len(tools) == len(TOOL_SPECS)  # count from source of truth, not a literal


def test_api_mcp_call_post(server):
    base, _ = server
    body = json.dumps({"name": "find_symbol", "arguments": {"name": "PricingCalculator"}}).encode()
    req = urllib.request.Request(base + "/api/mcp/call", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    assert data["ok"] and data["results"]


def test_static_assets(server):
    base, _ = server
    css, ctype = _get(base, "/static/app.css")
    assert b":root" in css and "css" in ctype
    js, _ = _get(base, "/static/app.js")
    assert b"initGraph" in js
