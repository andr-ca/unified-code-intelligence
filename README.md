# Unified Code Intelligence (UCI)

**A local-first code-intelligence platform where the source of truth is a knowledge graph — not
embeddings.** UCI parses a repository into a **canonical graph** of exact code relationships (calls,
imports, inheritance, tests, config, data, ownership, and more) and serves that one graph to both
**coding agents** (MCP/REST) and **humans** (web dashboard). Embeddings are one retrieval signal, not
the center of the system.

> Built by studying three excellent projects and unifying their best ideas:
> **CodeRAG** (local-first hybrid retrieval), **code-graph-rag** (Tree-sitter parsing + graph-as-truth
> + MCP), and **Understand-Anything** (the "graph that teaches" dashboard). See
> [`docs/repo-comparison.md`](docs/repo-comparison.md).

## Why graph-first?

Embedding-only tools answer "what looks similar?" but not the questions that matter for safe change:

- *Who calls this function?* · *What breaks if I change it?* · *Which tests cover it?*
- *What config controls this path?* · *What data does it touch?* · *Who owns it?*

UCI models these as **explicit, traceable edges**, so answers are deterministic and **every fact
cites a file and line range**. Embeddings then add fuzzy recall on top of a structurally correct core.

**Parsed today:** Python · JavaScript/TypeScript · config (.env/yaml/toml/json/ini) · and the
mainframe estate: **COBOL** (calls, copybooks, embedded SQL, VSAM files, paragraphs, CICS
commands, literal dataflow with taint tracking) · **JCL + PROC** (jobs→programs, DD datasets) ·
**CICS CSD** (transactions, files, mapsets) · **HLASM** (CSECT/EXTRN linkage) · **BMS** screens ·
DCLGEN→table lineage. Scored continuously against real repos in [`evals/`](evals/README.md)
(mainframe track ≈ 94/100).

## Design principles

1. **Graph is the source of truth.** Every extracted fact is a node or edge.
2. **Embeddings are one signal.** Retrieval works fully offline without them.
3. **One graph, two audiences.** Agents and humans read the same graph.
4. **Everything is traceable** to repo · path · line range.
5. **Adapters over vendors.** Storage, embeddings, parsing, and LLMs are pluggable.
6. **Local-first by default.** The default profile needs only Python's standard library + SQLite.

## Install

```bash
cd unified-code-intelligence
pip install -e .            # zero required dependencies (local-lite)
# optional extras:
pip install -e ".[embeddings]"   # real local ONNX embeddings (fastembed)
pip install -e ".[api]"          # FastAPI variant (the default dashboard uses the stdlib server)
pip install -e ".[all]"          # every optional adapter
```

No pip? Just `export PYTHONPATH=src` — UCI runs on the standard library alone.

## Quickstart

```bash
uci init                     # scaffold .uci/ and show the resolved profile
uci index .                  # scan → parse → graph → chunk → (embed) → git
uci query "where is pricing validated?"
uci impact PricingCalculator.calculate
uci graph symbol PricingCalculator.calculate
uci gaps                     # missing artifacts referenced but not indexed
uci serve                    # web dashboard at http://127.0.0.1:8765
uci mcp                      # MCP server (stdio) for coding agents
```

Try it on the bundled sample repo:

```bash
uci index tests/fixtures/sample_repo
uci impact PricingCalculator.calculate --path tests/fixtures/sample_repo
```

## The canonical graph

Entities span code, tests, data, runtime/config, ownership, business/domain, and legacy tiers
(`FUNCTION`, `CLASS`, `MODULE`, `TEST`, `DATABASE_TABLE`, `CONFIG_KEY`, `COMMIT`, `AUTHOR`,
`BUSINESS_CAPABILITY`, `LEGACY_PROGRAM`, `COPYBOOK`, `JCL_JOB`, …). Relationships include `CALLS`,
`IMPORTS`, `EXTENDS`, `IMPLEMENTS`, `DEFINES`, `TESTS`, `READS`/`WRITES`, `CONFIGURES`, `OWNS`,
`CHANGED`, `MAPS_TO`, `CANDIDATE_FOR_MIGRATION`, … Full taxonomy:
[`docs/canonical-schema.md`](docs/canonical-schema.md).

## Retrieval

Graph-first hybrid retrieval fuses **symbol · keyword · semantic · graph-expansion · file-proximity ·
churn** via Reciprocal Rank Fusion, with adaptive weighting for identifier vs. prose queries. Every
hit explains which signals fired and suggests next queries. Details:
[`docs/retrieval-strategy.md`](docs/retrieval-strategy.md).

The flagship query, *impact analysis*, returns a structured pack (callers, callees, tests, config,
data, churn, risk score) built by graph traversal — see it in action with `uci impact`.

## For coding agents (MCP)

`uci mcp` exposes structured-JSON tools: `search_code`, `find_symbol`, `get_callers`, `get_callees`,
`impact_analysis`, `explain_module`, `retrieve_edit_context`, `find_tests_for_symbol`,
`find_data_lineage`, `find_config_dependencies`, `list_index_gaps`. Register it with your agent:

```jsonc
{ "mcpServers": { "uci": { "command": "uci", "args": ["mcp", "--repo", "/path/to/repo"] } } }
```

Contracts: [`docs/mcp-tools.md`](docs/mcp-tools.md).

## For humans (dashboard)

`uci serve` renders repo overview, module list, symbol search, an offline **graph explorer**, symbol
detail, impact view, architecture map, a **gaps** panel (known unknowns), and an onboarding guide — all
clients of the same graph.

## Deployment profiles

| Profile | Metadata | Graph | Vector | Embeddings | External services |
| --- | --- | --- | --- | --- | --- |
| **local-lite** (default) | SQLite | SQLite/in-memory | SQLite/numpy | hash (or none) | none |
| **local-pro** | SQLite | Memgraph | Qdrant | Ollama | `docker-compose.local-pro.yml` |
| **cloud** | Postgres | Neo4j/Memgraph | Qdrant | OpenAI/Anthropic/Gemini | orchestrated |

Backends are selected declaratively (env or `--flags`); no core code imports a vendor SDK. Optional
adapters live behind interfaces (`GraphStore`, `VectorStore`, `MetadataStore`, `EmbeddingProvider`)
and import their SDK lazily. See [`.env.example`](.env.example).

## Architecture

```
ingest → parser → core.normalize → graph store        (source of truth)
   │        │                          │
   └ git    └ chunking → embeddings → vector store      (one retrieval signal)

retrieval (hybrid + impact)  →  CLI · MCP · REST · Web  (all over one Engine facade)
```

Module map and rationale: [`docs/architecture.md`](docs/architecture.md).

## Development & tests

```bash
PYTHONPATH=src python3 -m pytest -q        # 129 tests, no Docker, no network
```

Store interfaces have **contract tests** that run identically against the in-memory and SQLite
backends. Optional-backend tests are marked `@pytest.mark.optional_backend` and skipped unless the
backend is available.

## Security & honesty

- **Trust boundary:** `.uci/uci.db` stores source text + facts; treat it as repo-sensitive (git-ignored
  by default; don't sync). Config-language files are **never** chunked/embedded, and code chunks pass a
  best-effort secret scrubber, so `.env` values never reach vectors or any cloud embedding API.
- **Honest resolution:** call edges are labeled by how they were resolved; impact results are stratified
  into `resolved` / `candidates` / `unresolved` with a computed `completeness`, and every envelope reports
  index `staleness`. In local-lite the semantic signal is honestly labeled `lexical-hash` (token overlap),
  not learned semantics — upgrade with the `embeddings` extra or Ollama for real semantic recall.

## Roadmap
Phase 1 (MVP graph + vector + CLI), Phase 2 (MCP + better retrieval), and a Phase 3 dashboard are
implemented here. Phases 4 (non-semantic relationships) and 5 (legacy modernization) are schema-ready
with runnable previews in [`examples/`](examples). Full plan: [`docs/roadmap.md`](docs/roadmap.md).

## Examples

Seven end-to-end scenarios (semantic search, call graph, impact, test discovery, config deps, data
lineage, legacy modernization): [`examples/README.md`](examples/README.md).

## License

Apache-2.0. UCI is a clean-room re-architecture that reuses *ideas* from the three permissively
licensed reference projects (MIT / Apache-2.0 / MIT); it vendors none of their source and requires no
third-party code for the local-lite profile.
