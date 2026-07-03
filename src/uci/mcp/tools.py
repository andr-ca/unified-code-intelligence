"""MCP tool definitions and dispatch.

Tools are thin wrappers over :class:`uci.engine.Engine`, so MCP behavior matches the CLI/API exactly.
Every tool returns structured JSON (ids, paths, line ranges, reasons, confidence, next queries).
"""

from __future__ import annotations

from typing import Any

from ..engine import Engine

# JSON-schema tool descriptors (transport-agnostic; usable by any MCP client).
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search_code",
        "description": "Hybrid graph-first code search (symbol + keyword + semantic + graph). "
                       "Returns ranked entities with the signals and reason each was included.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language or identifier query"},
                "top_k": {"type": "integer", "default": 10},
                "kinds": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_symbol",
        "description": "Resolve a symbol name to its definition site(s) in the graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "exact": {"type": "boolean", "default": True},
                "kind": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_callers",
        "description": "Who calls this symbol (reverse call graph), with relationship paths.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "depth": {"type": "integer", "default": 1}},
            "required": ["symbol"],
        },
    },
    {
        "name": "get_callees",
        "description": "What this symbol calls (forward call graph), with relationship paths.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "depth": {"type": "integer", "default": 1}},
            "required": ["symbol"],
        },
    },
    {
        "name": "impact_analysis",
        "description": "What breaks if I change X? Structured impact pack: callers, callees, tests, "
                       "config, data, churn, and a risk score.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol_or_file": {"type": "string"}},
            "required": ["symbol_or_file"],
        },
    },
    {
        "name": "explain_module",
        "description": "Overview of a module/file: purpose, layer, defined symbols, imports, importers.",
        "inputSchema": {
            "type": "object",
            "properties": {"module_or_path": {"type": "string"}},
            "required": ["module_or_path"],
        },
    },
    {
        "name": "control_flow",
        "description": "Control-flow graph (block scheme) of a function/method: decisions, loops, "
                       "branches, calls, and returns as nodes with source lines, plus a Mermaid "
                       "flowchart. Shows the logic *inside* a routine. Python today.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "retrieve_edit_context",
        "description": "Everything needed to safely edit a symbol: source, callers/callees with "
                       "source, tests, imports, and an edit checklist.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "find_tests_for_symbol",
        "description": "Tests covering a symbol (via TESTS edges, test call sites, and name match).",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "find_data_lineage",
        "description": "Data reads/writes/mappings reachable from a symbol or table (READS/WRITES/MAPS_TO).",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol_or_table": {"type": "string"}},
            "required": ["symbol_or_table"],
        },
    },
    {
        "name": "find_config_dependencies",
        "description": "Config keys / feature flags that configure or control a component or path.",
        "inputSchema": {
            "type": "object",
            "properties": {"component_or_path": {"type": "string"}},
            "required": ["component_or_path"],
        },
    },
    {
        "name": "get_code_metrics",
        "description": "Codebase metrics from index time: LOC per language (code/comment/blank), "
                       "files, entry points (jobs/transactions/uncalled programs/__main__ guards), "
                       "cross-file dependency counts, call-resolution distribution, fan-in hubs.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_index_gaps",
        "description": "Known unknowns: artifacts referenced but not indexed (missing modules / "
                       "copybooks / programs), ranked by fan-in — the acquisition checklist for "
                       "partially-available codebases.",
        "inputSchema": {
            "type": "object",
            "properties": {"kind": {"type": "string", "description": "filter by artifact_kind"}},
        },
    },
]


def list_tools(engine: "Engine | None" = None) -> list[dict[str, Any]]:
    """List tool descriptors. When an engine is provided, annotate each tool with ``available``
    (whether the current index has supporting facts) so agents don't call always-empty tools."""
    if engine is None:
        return TOOL_SPECS
    caps = engine.capabilities()
    out: list[dict[str, Any]] = []
    for spec in TOOL_SPECS:
        entry = dict(spec)
        entry["available"] = caps.get(spec["name"], True)
        if not entry["available"]:
            entry["description"] += " (no supporting facts in the current index yet)"
        out.append(entry)
    return out


def dispatch(engine: Engine, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route an MCP tool call to the engine. Returns a structured result dict."""
    args = arguments or {}
    if name == "search_code":
        kinds = _kinds(args.get("kinds"))
        return engine.search(args["query"], top_k=int(args.get("top_k", 10)), kinds=kinds)
    if name == "find_symbol":
        return engine.find_symbol(args["name"], exact=bool(args.get("exact", True)), kind=args.get("kind"))
    if name == "get_callers":
        return engine.callers(args["symbol"], depth=int(args.get("depth", 1)))
    if name == "get_callees":
        return engine.callees(args["symbol"], depth=int(args.get("depth", 1)))
    if name == "impact_analysis":
        return engine.impact(args["symbol_or_file"])
    if name == "explain_module":
        return engine.explain_module(args["module_or_path"])
    if name == "control_flow":
        return engine.control_flow(args["symbol"])
    if name == "retrieve_edit_context":
        return engine.edit_context(args["symbol"])
    if name == "find_tests_for_symbol":
        return engine.find_tests_for_symbol(args["symbol"])
    if name == "find_data_lineage":
        return engine.find_data_lineage(args["symbol_or_table"])
    if name == "find_config_dependencies":
        return engine.find_config_dependencies(args["component_or_path"])
    if name == "get_code_metrics":
        return engine.metrics()
    if name == "list_index_gaps":
        return engine.gaps(args.get("kind"))
    return {"ok": False, "error": {"code": "unknown_tool", "message": name}}


def _kinds(raw: Any):
    if not raw:
        return None
    from ..core.entities import EntityType

    out = []
    for item in raw:
        try:
            out.append(EntityType(item))
        except ValueError:
            continue
    return out or None


__all__ = ["TOOL_SPECS", "list_tools", "dispatch"]
