"""MCP server tests: protocol handling and tool dispatch."""

from __future__ import annotations

import json

from uci.mcp.server import MCPServer
from uci.mcp.tools import TOOL_SPECS, list_tools


def _req(method, params=None, req_id=1):
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}


def test_initialize(indexed_engine):
    srv = MCPServer(indexed_engine)
    resp = srv.handle_request(_req("initialize"))
    assert resp["result"]["serverInfo"]["name"] == "unified-code-intelligence"
    assert "tools" in resp["result"]["capabilities"]


def test_tools_list_has_all_documented_tools(indexed_engine):
    srv = MCPServer(indexed_engine)
    resp = srv.handle_request(_req("tools/list"))
    names = {t["name"] for t in resp["result"]["tools"]}
    expected = {
        "search_code", "find_symbol", "get_callers", "get_callees", "impact_analysis",
        "explain_module", "retrieve_edit_context", "find_tests_for_symbol",
        "find_data_lineage", "find_config_dependencies",
    }
    assert expected <= names
    assert len(TOOL_SPECS) == len(list_tools())


def test_tools_call_impact(indexed_engine):
    srv = MCPServer(indexed_engine)
    resp = srv.handle_request(_req("tools/call", {
        "name": "impact_analysis", "arguments": {"symbol_or_file": "PricingCalculator.calculate"}
    }))
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] and payload["target"]["name"] == "calculate"


def test_tools_call_get_callers(indexed_engine):
    srv = MCPServer(indexed_engine)
    resp = srv.handle_request(_req("tools/call", {
        "name": "get_callers", "arguments": {"symbol": "PricingCalculator.calculate"}
    }))
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert any(r["name"] == "place_order" for r in payload["results"])


def test_notification_returns_none(indexed_engine):
    srv = MCPServer(indexed_engine)
    assert srv.handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_errors(indexed_engine):
    srv = MCPServer(indexed_engine)
    resp = srv.handle_request(_req("nope/nope"))
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_structured_error(indexed_engine):
    srv = MCPServer(indexed_engine)
    resp = srv.handle_request(_req("tools/call", {"name": "bogus", "arguments": {}}))
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] is False


def test_tools_list_annotates_availability(indexed_engine):
    srv = MCPServer(indexed_engine)
    tools = {t["name"]: t for t in srv.handle_request(_req("tools/list"))["result"]["tools"]}
    # sample repo has config keys but no data-flow edges
    assert tools["find_config_dependencies"]["available"] is True
    assert tools["find_data_lineage"]["available"] is False
    assert tools["search_code"]["available"] is True


def test_docs_tools_listed_and_dispatch(tmp_path):
    from uci import Config, Engine
    from uci.mcp.tools import dispatch, list_tools

    (tmp_path / "cbl").mkdir()
    (tmp_path / "cbl" / "COSGN00C.cbl").write_text(
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. COSGN00C.\n"
        "       PROCEDURE DIVISION.\n           MOVE 1 TO X.\n")
    (tmp_path / "README.md").write_text(
        "# App\n\n## Signon — COSGN00C\n\n`COSGN00C` handles signon.\n")
    with Engine(Config.from_env(tmp_path)) as eng:
        eng.index(full=True)
        names = {t["name"] for t in list_tools(eng)}
        assert {"search_docs", "get_documentation"} <= names
        res = dispatch(eng, "get_documentation", {"symbol": "COSGN00C"})
        assert res["documentation"] and res["documentation"][0]["path"] == "README.md"
        res = dispatch(eng, "search_docs", {"query": "signon"})
        assert res["results"] and all(h["kind"] == "doc_section" for h in res["results"])
