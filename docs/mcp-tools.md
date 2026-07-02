# UCI MCP Tools

UCI exposes a Model Context Protocol (MCP) server (`uci mcp`) so coding agents can query **exact code
relationships** from the canonical graph. Tools return **structured JSON**, not prose. The server is a
thin adapter over the same retrieval/analysis code used by the CLI and API (single source of truth).

Transport: newline-delimited JSON-RPC 2.0 over stdio (no third-party MCP SDK required for local-lite;
an official-SDK adapter can be added later without changing tool logic).

## 1. Common result envelope

Every tool result shares a consistent, explainable shape:

```jsonc
{
  "ok": true,
  "tool": "impact_analysis",
  "query": "PricingCalculator.calculate",
  "results": [
    {
      "entity_id": "method:repo:pricing/calculator.py:pricing.calculator.PricingCalculator.calculate",
      "kind": "method",
      "name": "calculate",
      "path": "pricing/calculator.py",
      "start_line": 12,
      "end_line": 18,
      "score": 0.91,
      "signals": ["symbol", "graph"],
      "relationship_path": ["CALLS<-", "checkout.py:place_order"],
      "reason": "Direct caller of the changed method",
      "confidence": 0.95
    }
  ],
  "next_queries": ["get_tests_for_symbol PricingCalculator.calculate",
                   "find_config_dependencies pricing"],
  "stats": { "elapsed_ms": 7, "signals_used": ["symbol", "graph"] }
}
```

Fields required on every hit: `entity_id`, `path`, `start_line`/`end_line`, `reason`, `confidence`, plus
`relationship_path` where a graph path exists. Errors return `{ "ok": false, "error": {code, message} }`.

## 2. Tool catalog

| Tool | Input | Returns |
| --- | --- | --- |
| `search_code` | `query`, `top_k?`, `kinds?` | Hybrid hits with signals + reasons |
| `find_symbol` | `name`, `exact?`, `kind?` | Matching symbols with definition site |
| `get_callers` | `symbol`, `depth?` | Reverse-CALLS neighbors with paths |
| `get_callees` | `symbol`, `depth?` | Forward-CALLS neighbors with paths |
| `impact_analysis` | `symbol_or_file` | Full impact pack (callers/callees/tests/config/data/**overrides**/churn/risk) |
| `explain_module` | `module_or_path` | Overview: purpose, key symbols, deps, layer, entry points |
| `retrieve_edit_context` | `symbol` | Source + callers/callees/tests/imports + edit checklist |
| `find_tests_for_symbol` | `symbol` | Tests via reverse-TESTS + heuristic name match |
| `find_data_lineage` | `symbol_or_table` | READS/WRITES/MAPS_TO chains (data flow) |
| `find_config_dependencies` | `component_or_path` | CONFIGURES/CONTROLS keys & flags reaching the target |
| `get_code_metrics` | — | Index-time codebase metrics: LOC per language (code/comment/blank), files, entry points, cross-file dependency counts, call-resolution distribution, fan-in hubs |
| `list_index_gaps` | `kind?` | Missing artifacts referenced but not indexed, ranked by fan-in (the acquisition checklist) |

All twelve tools are wired. `find_data_lineage`, `find_config_dependencies`, `get_code_metrics`, and `list_index_gaps`
reflect the facts present in the current index (config keys and gaps are populated in the MVP; SQL/data
lineage grows in Phase 4), and `tools/list` advertises an `available` flag per tool so agents skip
always-empty ones.

### Honesty features (from the review)
- **Stratification:** `impact_analysis` returns `callers`/`callees` as `{resolved, candidates, unresolved}`;
  each hit carries a `resolution` label and derived `confidence`. `get_callers`/`get_callees` label every
  hit's `resolution` and gate multi-hop traversal to resolved edges. Both `callers` and `callees` report
  an `unresolved` block, so hidden (dynamic) callers *and* callees are surfaced identically.
- **Completeness & staleness:** results include a computed `completeness` and an `index` block
  (`generation`, `head_sha`, `commits_behind`); capped traversals report `truncated`/`limit`.
- **Dynamic availability:** `tools/list` annotates each tool with `available` based on which edge types
  exist in the current index, so agents don't call always-empty tools.
- **Stub labeling:** results carry `missing`/`external` flags; placeholder (unindexed) entities are
  labeled in graph/impact results and excluded from `search`/`find_symbol`, so an agent never tries to
  open source that doesn't exist.

## 3. Selected tool contracts

### `search_code`
```jsonc
// in
{ "query": "where is pricing validation implemented?", "top_k": 8 }
// out: results[] as in the envelope, each with signals[] and reason
```

### `get_callers` / `get_callees`
```jsonc
// in
{ "symbol": "PricingCalculator.calculate", "depth": 2 }
// out: neighbors with relationship_path showing each hop and its call-site line range
```

### `impact_analysis`
```jsonc
// in
{ "symbol_or_file": "pricing/calculator.py:PricingCalculator.calculate" }
// out
{
  "ok": true,
  "target": { "entity_id": "...", "path": "...", "start_line": 12, "end_line": 18 },
  "callers":  [ /* hits */ ],
  "callees":  [ /* hits */ ],
  "tests":    [ /* hits */ ],
  "config":   [ /* hits */ ],
  "data":     [ /* hits */ ],
  "churn":    { "commits_90d": 4, "authors": ["a@x"], "last_changed": "2026-06-20" },
  "risk":     { "score": 0.72, "level": "high", "factors": ["8 callers", "no direct tests", "recent churn"] },
  "next_queries": ["find_tests_for_symbol ...", "retrieve_edit_context ..."]
}
```

### `retrieve_edit_context`
```jsonc
// out
{
  "target": { "path": "...", "start_line": 12, "end_line": 18, "source": "def calculate(...): ..." },
  "callers": [ { "path": "...", "lines": [40, 44], "source": "..." } ],
  "callees": [ ... ],
  "tests":   [ ... ],
  "imports": [ "pricing.rules.DiscountRule" ],
  "checklist": [
    "Update 3 callers in checkout.py, api/orders.py",
    "Re-run 2 covering tests in tests/test_pricing.py",
    "Preserve DiscountRule.apply() contract"
  ]
}
```

## 4. Safety
- **Read-only by default.** MVP tools never modify files. Edit-oriented tools return *context and
  checklists*, leaving the actual edit to the agent + human review (no unsandboxed shell tool).
- **Bounded traversal.** Depth and result caps prevent runaway expansions.
- **No secrets in output.** Config *keys* are surfaced; values are redacted unless explicitly allowed.
- **Deterministic IDs.** Agents can round-trip an `entity_id` back into any tool.

## 5. Registering with an agent

```jsonc
// Example MCP client config
{
  "mcpServers": {
    "uci": { "command": "uci", "args": ["mcp", "--repo", "/path/to/repo"] }
  }
}
```

Because all tools sit on the same core as the CLI/API, behavior is identical across surfaces.
