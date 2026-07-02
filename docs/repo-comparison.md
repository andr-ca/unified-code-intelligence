# Repo Comparison — code-graph-rag vs CodeRAG vs Understand-Anything

This document captures the inspection of the three reference projects that inspire
**Unified Code Intelligence (UCI)**. It records each project's architecture, key modules,
storage, indexing, retrieval, agent/UI surfaces, reusable ideas, limitations, and licensing.

> **Bottom line for UCI:** take CodeRAG's local-first DX + incremental hybrid retrieval,
> code-graph-rag's Tree-sitter parsing + graph-as-truth + MCP tools, and
> Understand-Anything's "graph that teaches" multi-view dashboard — but unify them behind a
> **single canonical knowledge graph** that is the source of truth for both agents and humans,
> with embeddings as *one* retrieval signal.

---

## 1. code-graph-rag (`codebase_rag`)

| Aspect | Finding |
| --- | --- |
| **License** | MIT (permissive; safe to learn from) |
| **Language** | Python |
| **Core idea** | Parse a repo into a **graph database**, then answer structural questions via NL→Cypher and expose MCP tools to agents. |

**Architecture / data flow:** `ingest → parse → extract symbols → build graph → query/retrieve`.
Orchestrated by `graph_updater.py`; a `ProcessorFactory` wires four processors
(Definition, Structure, Import, Call) plus `type_inference`. A trie-based
`FunctionRegistryTrie` maintains qualified-name (QN) identity with `@line` collision handling.

**Key modules:** `parser_loader.py` + `language_spec.py` (Tree-sitter loading and per-language
specs), `parsers/handlers/*` (10+ languages), `services/graph_service.py` (Memgraph),
`services/llm.py` (NL→Cypher with validation), `vector_store.py` (Qdrant), `embedder.py` +
`unixcoder.py` (768-d code embeddings), `mcp/server.py` + `mcp/tools.py`.

**Storage:** Memgraph (graph) + Qdrant (vectors) + optional protobuf export + JSON embedding cache.

**Parsing:** Tree-sitter with combined function/class/import/call queries; QNs resolved per
language spec; call graph via type inference.

**Graph schema:** Nodes `Project/Package/Folder/File/Module/Function/Class/Method/Interface/Enum/Type/ExternalPackage`;
relationships `CONTAINS_*, DEFINES, DEFINES_METHOD, INHERITS, IMPLEMENTS, CALLS, INSTANTIATES, IMPORTS, DEPENDS_ON`.

**Retrieval:** NL→Cypher (LLM) with an allow-list validator + timeout; semantic search via Qdrant;
graph traversal for exact structure (e.g., dead-code reachability).

**MCP:** `query_codebase_knowledge_graph`, `get_code_snippet`, `semantic_search_code`,
`edit_code`, `read/write_file`, `list_directory`, `execute_shell_command`, project management.

**Reusable concepts:** language spec/handler abstraction; processor factory; trie symbol registry;
**pluggable ingestor protocol**; LLM query generation *with validation*; MCP-as-thin-adapter.

**Limitations:** hard dependency on Memgraph (+ Qdrant); needs external services running; no true
incremental indexing (full re-parse); in-memory trie is RAM-bound; NL→Cypher can hallucinate; shell
tool is a security surface.

---

## 2. CodeRAG (`coderag`)

| Aspect | Finding |
| --- | --- |
| **License** | Apache-2.0 (permissive; patent grant) |
| **Language** | Python |
| **Core idea** | **Local-first** semantic + lexical code search with zero external services and great DX. |

**Architecture / data flow:** a `CodeRAG` facade (`api.py`) lazily wires provider → store →
indexer → searcher → reranker. Indexing: walk (gitignore + globs) → content-hash change detection →
symbol-aware chunking → local embeddings → batched LanceDB writes. Query: embed → parallel dense ANN
+ BM25 → **Reciprocal Rank Fusion** → optional graph 1-hop → optional cross-encoder rerank.

**Key modules:** `indexer.py` (incremental, SHA-256 authority, delete-before-add),
`chunking/*` (Python `ast` + Tree-sitter, line-ownership smallest-span, window fallback),
`embeddings/*` (runtime-checkable provider protocol: fastembed local ONNX / OpenAI / fake),
`store/lance_store.py` (one LanceDB DB: `chunks` + `files` tables, BM25 + ANN),
`retrieval/*` (`fusion.py` RRF, `query_type.py` adaptive weights, `rerank.py`, `graph.py` callee expansion),
`surfaces/*` (CLI, HTTP, webui, MCP), `watch.py` (debounced watchdog).

**Storage:** LanceDB embedded columnar store under `.coderag/` (vectors + BM25 + file metadata).

**Indexing:** `size+mtime` fast-path → SHA-256 hash authority → re-embed only changed files;
symbol-aware chunks with sliding-window gap fill; live partial flushing.

**Embeddings:** pluggable protocol; default local ONNX `BAAI/bge-small-en-v1.5` (384-d), no key.

**Retrieval:** hybrid dense+BM25 via **RRF** (robust to incomparable scales); adaptive fusion
(identifier vs prose); optional cross-encoder rerank (+5–15% MRR); optional call-graph expansion.

**Surfaces:** CLI / Python lib / HTTP+API-key / built-in web UI / MCP (`search_code`, `search_files`,
`get_file`, `index_status`, `index`). One-line install auto-registers with agents.

**Reusable concepts:** provider protocol + lazy DI; content-hash incremental; **RRF fusion**;
adaptive query-type routing; two-stage retrieve-then-rerank; thin MCP over one core facade;
thread-safe `IndexProgress` polling; unified store.

**Limitations:** embeddings are **central to recall** (the thing UCI explicitly fixes); the symbol
graph is underused (callee-only, off by default, no multi-hop, no caller graph); no persistent
structural graph to answer exact relationship questions.

---

## 3. Understand-Anything (TS/Node monorepo)

| Aspect | Finding |
| --- | --- |
| **License** | MIT (permissive) |
| **Language** | TypeScript (pnpm workspace) |
| **Core idea** | A **"graph that teaches"** — human-facing knowledge-graph dashboard with multiple views and guided onboarding. |

**Architecture:** `packages/core` (Tree-sitter extraction + LLM analysis + graph build + search),
`packages/dashboard` (React 19 + Vite + `@xyflow/react` + Tailwind + Zustand), `homepage` (Astro).
A multi-agent pipeline (`project-scanner → file-analyzer → architecture-analyzer → domain-analyzer →
tour-builder`) produces JSON graphs in `.understand-anything/`.

**Knowledge graph:** **21 node types + 35 edge types** (`types.ts`, `schema.ts`) spanning code,
config/infra, data, domain/business, and knowledge/wiki. A schema layer validates and **aliases**
LLM output (`func → function`, `extends → inherits`). Structure comes from Tree-sitter (deterministic);
semantics (summaries, tags, complexity, layers, domains) come from an LLM.

**Storage:** JSON snapshots (`knowledge-graph.json`, `domain-graph.json`, `meta.json`,
`fingerprints.json`, `config.json`) with **path sanitization** (strips home dir) and fingerprint-based
incremental updates.

**Human UX:** three mental models — **Structural / Domain-Business / Knowledge-Wiki** — over the same
graph; **persona filtering** (non-technical / junior / experienced); guided **tours** via topological
sort; **diff impact** overlay; ELK/Dagre/d3-force layouts; inline code viewer; i18n.

**Reusable concepts:** **KG as source of truth with multiple presentation views**; persona-adaptive
detail; incremental fingerprints; guided onboarding by dependency order; layer-first visualization;
Tree-sitter (structure) + LLM (semantics) split; schema aliasing/auto-fix; diff→graph traversal for impact.

**Limitations:** the analysis is a **separate JSON snapshot layer**, not a queryable canonical graph
that agents can also use. Views are consistent with each other but the graph is not a shared runtime
service. Agent retrieval and human exploration are effectively two systems.

---

## 4. Side-by-side summary

| Dimension | code-graph-rag | CodeRAG | Understand-Anything |
| --- | --- | --- | --- |
| Source of truth | Graph DB (Memgraph) | Vector store (LanceDB) | JSON graph snapshot |
| Parsing | Tree-sitter (10+ langs) | Python `ast` + Tree-sitter | Tree-sitter (12 langs) + parsers |
| Embeddings role | Complementary | **Central** | Optional (semantic search) |
| Structural graph | **Rich** (calls/imports/inherit) | Minimal (callee 1-hop) | Rich (21×35) but LLM-derived |
| Retrieval | NL→Cypher + vector | **Hybrid RRF + rerank** | Fuzzy + semantic |
| Incremental | Partial (hash/mtime) | **Strong** (hash authority) | Fingerprints |
| Agent surface | **MCP + edit tools** | MCP (search/get_file) | Plugin context builders |
| Human surface | — | Minimal web UI | **Rich multi-view dashboard** |
| External services | Memgraph + Qdrant | **None (local-first)** | None (JSON) |
| License | MIT | Apache-2.0 | MIT |

## 5. What UCI takes from each

- **From CodeRAG:** local-first zero-dependency DX, content-hash incremental indexing, symbol-aware
  chunking, provider abstraction, **RRF hybrid retrieval**, adaptive fusion, optional rerank, thin
  surfaces over one facade. *Improved:* embeddings are only one retrieval signal.
- **From code-graph-rag:** Tree-sitter parsing, symbol/call/import extraction, **graph-as-truth**,
  pluggable persistence protocol, MCP agent tools, NL query interface. *Improved:* entities normalized
  into a **broader canonical schema** (data, tests, config, runtime, ownership, domain, legacy).
- **From Understand-Anything:** multi-view "graph that teaches" dashboard, persona filtering, guided
  onboarding, layer/architecture views, diff→impact. *Improved:* the UI is a **client of the same
  canonical graph** the agents query — not a separate analysis layer.

## 6. Licensing conclusion
All three are permissive (MIT / Apache-2.0 / MIT). UCI is a **clean-room re-architecture** that reuses
*ideas and patterns*, not source. UCI ships under a permissive license and vendors no third-party code.
Optional adapters (Memgraph, Neo4j, Qdrant, LanceDB, Postgres, Ollama, OpenAI/Anthropic/Gemini) are
loaded behind interfaces and are never required for the local-lite profile.

## 7. Prior art beyond the three references (and where UCI fits)

The three reference projects are RAG-era tools, but UCI's structural claims (*who calls this?*, *what
breaks?*) belong to the mature field of **code indexing / static analysis**. Honest positioning:

| System | What it already does | What UCI adds / how it differs |
| --- | --- | --- |
| **LSP servers** | Type-aware `callHierarchy`, `references`, `definition` per language | Persistence + cross-language uniformity + **non-code entities** (config/tests/churn/data/legacy) + queryable without a running language server. UCI can *ingest* LSP/SCIP later to gain type precision. |
| **SCIP / Sourcegraph** | Cross-repo precise indexes via typed indexers | UCI's `entity_id` solves the same problem as SCIP symbols; mapping to SCIP would buy interop with existing precise indexers. |
| **Glean (Meta), Kythe (Google)** | Canonical fact schemas over code at monorepo scale | UCI's canonical schema is the same idea at small scale; Glean's derived-facts/incremental-invalidation patterns inform UCI's Phase-4 evolution. |
| **CodeQL** | Code as a queryable relational database | UCI's impact pack is a fixed-shape specialization of general graph queries; a query surface is a natural extension. |

**UCI's niche:** local-first, zero-dependency, *unified* graph that (a) spans code **and** non-code
facts, (b) serves agents and humans from one source, and (c) is honest about heuristic vs. precise
edges (see the resolution ladder in `retrieval-strategy.md` §9). It is not a replacement for a
type-aware indexer; it is the connective, explainable layer above them.
