# UCI Retrieval Strategy

UCI retrieval is **graph-first, embedding-assisted**. The graph answers exact structural questions;
embeddings add fuzzy recall for natural-language queries. No single signal is required — retrieval
degrades gracefully when embeddings are unavailable.

## 1. Signals

| Signal | Source | Works without embeddings? |
| --- | --- | --- |
| **Symbol lookup** | exact/qualified-name match in metadata store | ✅ |
| **Keyword / lexical** | tokenized match over names, docstrings, chunk text (SQLite FTS or fallback) | ✅ |
| **Semantic** | vector similarity over symbol-aware chunks | ❌ (optional) |
| **Graph expansion** | 1..N-hop neighborhood over CALLS/IMPORTS/DEFINES/... | ✅ |
| **File proximity** | same file / same directory / same module | ✅ |
| **Churn / recency** | git commit + churn signal | ✅ (if git present) |

## 2. Fusion (Reciprocal Rank Fusion)

Signals return ranked candidate lists. We combine them with **RRF** (borrowed from CodeRAG) because it
is robust to incomparable score scales (cosine vs. BM25 vs. graph distance):

$$\text{score}(d) = \sum_{s \in \text{signals}} \frac{w_s}{k + \text{rank}_s(d) + 1}, \quad k = 60$$

Default weights favor exact structure: `symbol=1.4, keyword=1.0, semantic=1.0, graph=0.8, proximity=0.4,
churn=0.3`. **Adaptive routing** (from CodeRAG) detects whether the query names an identifier
(`snake_case`, `camelCase`, `dotted.path`, `func(`, backticks). If so, lexical/symbol weights stay high;
for prose it leans semantic. Weights and `rrf_k` are configurable via `UCI_WEIGHT_*` / `UCI_RRF_K`
(see `.env.example`).

> **Scaling note:** the MVP keyword signal scores entities/chunks in Python (O(n) per query) — fine at
> repo scale, but a SQLite FTS5 lexical index is planned before any large-scale benchmark.

## 3. Query pipeline

```
query
  │
  ├─ classify: identifier-like? question-like? file-path? symbol?
  │
  ├─ symbol lookup      ─┐
  ├─ keyword search      │
  ├─ semantic search     ├─▶ RRF fusion ─▶ graph expansion (1-hop) ─▶ re-fuse ─▶ top-K
  ├─ file proximity      │
  └─ churn boost        ─┘
  │
  ▼
structured hits: {entity_id, kind, name, path, start_line, end_line,
                  score, signals[], reason, relationship_path, next_queries[]}
```

Every hit explains **why** it was included (which signals fired, which relationship path connected it to
a seed) and suggests **next queries** — mirroring the "explainability over flash" principle.

## 4. Impact analysis (the flagship query)

For *"What breaks if I change `PricingCalculator.calculate()`?"* UCI builds a structured **impact pack**
by graph traversal, not embeddings:

```
resolve symbol  →  PricingCalculator.calculate  (METHOD node)
├─ callers      : reverse CALLS  (who calls it)                → direct blast radius
├─ callees      : forward CALLS  (what it depends on)          → what it needs
├─ tests        : reverse TESTS  (covering tests)              → safety net
├─ overrides    : EXTENDS/IMPLEMENTS siblings + method matches → polymorphic risk
├─ config       : CONFIGURES keys reaching this component      → behavior toggles
├─ data         : READS/WRITES tables/queues touched           → data lineage
├─ siblings     : same-capability / same-file symbols          → coincidental coupling
├─ churn        : recent COMMITs touching this symbol/file     → volatility signal
└─ risk score   : f(callers, tests?, churn, fan-out)           → prioritization
```

The result is JSON with entity IDs, file paths, line ranges, relationship paths, per-item reason, and a
computed **risk score**. The MVP implements callers, callees, tests, siblings, config (when present), and
churn; data-lineage/flag edges activate as those extractors land (see roadmap).

### Risk score (published, not opaque)

$$\text{risk} = \min(0.5,\, 0.06 n_{callers}) + \min(0.25,\, 0.03 c_{churn}) + \min(0.1,\, 0.01 f_{fanout}) + 0.25\,[\,\text{no direct tests}\,]$$

Every term is returned in `risk.factors` alongside the scalar, so an agent can re-weight or ignore it.
Levels: `high` ≥ 0.66, `medium` ≥ 0.33, else `low`.

### Stratified results + completeness

`impact_analysis` returns callers/callees split into **`resolved`** (R0–R3) and **`candidates`** (R4–R5),
plus an **`unresolved`** summary of call sites that couldn't be attributed, and a computed
**`completeness`** (`exact` | `partial` | `heuristic`). Envelopes also carry an **`index`** block
(`generation`, `head_sha`, `commits_behind`) so a stale graph is never mistaken for a current one.

## 5. Retrieve-edit context (for agents)

`retrieve_edit_context(symbol)` returns everything an agent needs to safely edit a symbol:

- the symbol's **source** (path + line range + text),
- **direct callers and callees** with source snippets,
- **tests** that must still pass,
- **imports** the symbol relies on,
- **sibling definitions** likely to need parallel edits,
- a suggested **edit checklist** derived from the graph.

This is the graph-native version of "grep around and hope," giving agents a precise, bounded context.

## 6. Reranking (optional)

When a cross-encoder provider is configured (later phase), a second-stage rerank re-scores the fused top-N
jointly with the query (CodeRAG pattern, +5–15% MRR). Off by default to keep local-lite dependency-free.

## 7. Degradation matrix

| Available | Query behavior |
| --- | --- |
| Graph + git + embeddings | Full hybrid + churn + semantic recall |
| Graph + git | Symbol + keyword + graph + proximity + churn |
| Graph only | Symbol + keyword + graph + proximity |
| Empty index | Clear "not indexed" guidance |

## 8. Why this beats embedding-only retrieval

- **Structure over similarity:** callers/tests/impact come from graph edges, not vector guesses.
- **Explainable:** every hit cites the relationship path, resolution level, and line range.
- **Complete-ish:** finds relevant-but-dissimilar code (a caller need not look like the callee),
  bounded by extraction quality (see §9).
- **Cheap:** works offline with zero models; embeddings are a bonus, not a bottleneck.

## 9. Call-graph resolution is heuristic — and labeled as such (the resolution ladder)

Be honest: without a full type system, `CALLS`/`REFERENCES` extraction for Python/JS is **name-based
and heuristic**. A bare `obj.calculate()` could match several `calculate` definitions; dynamic
dispatch, DI, decorators, and framework routing can miss or over-connect edges. UCI does **not**
claim these edges are provably correct. Instead every call edge carries a mandatory `resolution`
attribute and a derived (not invented) confidence, from a fixed ladder:

| Level | `resolution` | How the edge was derived | Confidence |
| --- | --- | --- | --- |
| R0 | `syntactic` | Same-scope / `self`.method within the defining class / unique same-module name | 1.0 |
| R1 | `import-traced` | Callee lives in a module the caller explicitly imports (follow the import edge, not the global namespace) | 0.95 |
| R3 | `inherited` | Method resolved through the `EXTENDS`/`IMPLEMENTS` chain | 0.9 |
| R4 | `name-match` | Exactly one symbol in the repo has this name | 0.6 |
| R5 | `candidate` | N>1 symbols share the name; edge kept with `fan_out=N` | ≤ 0.4 |
| — | *(dropped)* | Fan-out above the cap (noisy common names like `get`/`run`) | not an edge |

This makes "deterministic" a per-edge property you can point at and filter on, rather than a blanket
claim. Import tracing also *improves accuracy*: an ambiguous `calc.calculate()` in a file that
`import`s `PricingCalculator` resolves to the right method. Type-aware precision (annotations, LSP/SCIP
import) is on the roadmap to promote more edges into R0–R3.
