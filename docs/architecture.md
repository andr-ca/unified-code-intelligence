# UCI Architecture

**Unified Code Intelligence (UCI)** is a local-first code-intelligence platform whose **source of
truth is a canonical knowledge graph**. Embeddings are one retrieval signal among many. The same graph
powers both **agent retrieval** (MCP/API) and **human understanding** (web dashboard).

## 1. Design principles

1. **Graph is the source of truth.** Every extracted fact is a node or edge in the canonical graph.
2. **Embeddings are one signal.** Keyword, symbol, graph traversal, and file proximity work without them.
3. **One graph, two audiences.** Agents and humans read the same graph through different projections.
4. **Everything is traceable.** Each node/edge carries provenance: repo, file path, line range.
5. **Adapters over vendors.** Storage, embeddings, parsing, and LLM are pluggable interfaces.
6. **Local-first by default.** The `local-lite` profile runs with only Python's stdlib + SQLite.
7. **Incremental where it counts.** The graph is fully rebuilt each pass (cheap, always consistent — no
   stale cross-file edges); the *expensive* embedding step is content-hash incremental, so unchanged
   files are never re-embedded. True two-phase graph incrementality is roadmapped for very large repos.
8. **Explainability over flash.** Results say *why* they were included and *what to query next*.

## 2. Module map

Conceptual modules (the `uci-*` names in the brief) are implemented as subpackages of `uci`:

| Brief module | Package | Responsibility |
| --- | --- | --- |
| `uci-core` | `uci.core` | Canonical entity/relationship schema, stable IDs, normalization, provenance, shared types |
| `uci-ingest` | `uci.ingest` | Repo scanner, ignore rules, language detection, file hashing, git metadata, incremental orchestration |
| `uci-parser` | `uci.parser` | Parser abstraction + language plugins (Python, JS/TS, **COBOL, JCL/PROC, CICS CSD, HLASM, BMS**, config); symbol/import/call/reference extraction incl. mainframe RUNS/INVOKES/READS/WRITES/MAPS_TO/USES links |
| `uci-embeddings` | `uci.embeddings` | Symbol-aware chunking, embedding provider abstraction (Noop/Local/Ollama/OpenAI) |
| `uci-graph` | `uci.graph` | `GraphStore` interface, `InMemoryGraphStore`, `SQLiteGraphStore`, traversal API |
| `uci-store` | `uci.store` | `MetadataStore` (SQLite), `VectorStore` (SQLite/numpy), SQL schema |
| `uci-retrieval` | `uci.retrieval` | Hybrid retrieval, RRF fusion, keyword/symbol search, graph expansion, impact analysis |
| `uci-analysis` | `uci.analysis` | Repo overview, architecture/layer inference, onboarding, risk |
| `uci-mcp` | `uci.mcp` | MCP server (stdio JSON-RPC) exposing agent tools |
| `uci-api` | `uci.api` | FastAPI REST API + server-rendered dashboard routes |
| `uci-web` | `uci.api` + `web/` | Dashboard (overview, modules, search, graph explorer, symbol detail, impact, architecture, onboarding) |
| `uci-cli` | `uci.cli` | `uci init/index/watch/query/graph/impact/serve/mcp` |

## 3. Data-flow pipeline

```
                          ┌──────────────────────────────────────────────┐
                          │                 uci index                    │
                          └──────────────────────────────────────────────┘
  repo path
     │
     ▼
┌────────────┐   files    ┌────────────┐  symbols/   ┌──────────────┐  entities/  ┌───────────────┐
│  ingest    │──────────▶ │  parser    │──imports/──▶│ core.normalize│──relations─▶│  graph store  │
│ scan+hash  │            │ py / js-ts │   calls     │  canonicalize │             │ sqlite+memory │
└─────┬──────┘            └─────┬──────┘             └──────┬────────┘             └──────┬────────┘
      │ changed files           │ chunks                    │ provenance                  │
      ▼                         ▼                            ▼                             │
┌────────────┐          ┌───────────────┐            ┌───────────────┐                     │
│ git meta   │          │  chunking     │            │ metadata store│◀────────────────────┘
│ churn/blame│          │ symbol-aware  │            │  sqlite       │
└────────────┘          └──────┬────────┘            └───────────────┘
                               ▼
                        ┌───────────────┐            ┌───────────────┐
                        │ embeddings    │──vectors──▶│ vector store  │
                        │ provider(opt) │            │ sqlite/numpy  │
                        └───────────────┘            └───────────────┘

                          ┌──────────────────────────────────────────────┐
                          │        uci query / impact / serve / mcp      │
                          └──────────────────────────────────────────────┘
   query
     │
     ▼
┌──────────────────────────── retrieval ────────────────────────────┐
│  keyword+symbol  │  semantic (if vectors)  │  graph expansion       │
│        └──────────────── RRF fusion ───────────────┘                │
│                     + file proximity + churn signal                 │
└───────────────┬───────────────────────────────────┬────────────────┘
                ▼                                     ▼
        structured result                       impact pack
     (ids, paths, line ranges,              (callers, callees, tests,
      relationship paths, reason,            config, churn, related)
      confidence, next queries)
                │
        ┌───────┴────────┬─────────────────┬────────────────┐
        ▼                ▼                 ▼                ▼
     CLI (text)      MCP (JSON)        REST API         Web dashboard
```

## 4. Storage architecture

Everything persists to a single SQLite database (`.uci/uci.db`) by default. The in-memory graph is
**hydrated from SQLite** for fast traversal; writes go to both.

```
┌──────────────────────────── uci.store.MetadataStore ─────────────────────────────┐
│ repositories · files · symbols · relationships · chunks · embeddings(meta+vector) │
│ file_hashes · index_state · git_commits · git_churn                               │
└──────────────────────────────────────────────────────────────────────────────────┘
        ▲                         ▲                          ▲
        │                         │                          │
┌───────┴────────┐      ┌─────────┴─────────┐      ┌─────────┴──────────┐
│  GraphStore    │      │   VectorStore     │      │  (adapters, later) │
│  InMemory      │      │   SQLite / numpy  │      │  Memgraph / Neo4j  │
│  SQLite        │      │   (brute-force)   │      │  Qdrant / LanceDB  │
└────────────────┘      └───────────────────┘      │  Postgres / pgvec  │
                                                    └────────────────────┘
```

**Interfaces (`uci.core.interfaces`):** `GraphStore`, `VectorStore`, `MetadataStore`,
`EmbeddingProvider`. Adapters are selected declaratively from `Config`. No core module imports a
vendor SDK; optional adapters import their SDK lazily and fail with a clear message if missing.

## 5. Deployment profiles

| Profile | Metadata | Graph | Vector | Embeddings | Docker |
| --- | --- | --- | --- | --- | --- |
| **local-lite** (default) | SQLite | InMemory+SQLite | SQLite/numpy | Noop or Local(hash) | none |
| **local-pro** | SQLite/Postgres | Memgraph | Qdrant | Ollama | `docker-compose.local-pro.yml` |
| **cloud/dev-team** | Postgres | Neo4j/Memgraph | Qdrant | OpenAI/Anthropic/Gemini | orchestrated |

The MVP fully implements **local-lite**. `local-pro`/`cloud` adapters are scaffolded behind interfaces
with clear extension points (see `docs/roadmap.md`).

## 6. Why graph-first (not embedding-first)

Embedding-centric systems answer "what looks similar?" well but cannot reliably answer exact structural
questions: *who calls this?*, *what breaks if I change it?*, *which tests cover it?*, *what config
controls this path?*. UCI models these as **explicit edges** so answers are explainable and traceable to
line ranges, with each call edge **labeled by how it was resolved** (exact same-scope/import-traced vs.
heuristic name-match — see the resolution ladder in `docs/retrieval-strategy.md` §9). Embeddings then add
fuzzy recall for natural-language queries on top of that structural core.

## 7. Component boundaries & testing
- **Contract tests** run the same suite against every implementation of each interface
  (`InMemoryGraphStore` and `SQLiteGraphStore`; SQLite vs numpy vector store).
- **Fixtures** are tiny sample repos committed under `tests/fixtures/` and `examples/`.
- **CI** runs with no Docker and no network. Optional-backend tests are marked with pytest markers
  (`@pytest.mark.optional_backend`) and skipped unless the backend is available.
- Every stage (ingest → parse → normalize → store → retrieve) is independently testable.

## 8. Trust boundary

`.uci/uci.db` contains **source text** (chunk bodies) and derived facts. Treat it with the same
sensitivity as the repository itself: it is git-ignored by default, should not be synced to shared
locations, and should be excluded from backups that the repo itself is excluded from. Secrets are
reduced at ingest — config-language files are never chunked/embedded, and code chunks pass through a
best-effort secret scrubber — but the database should still be treated as repo-sensitive.
