# UCI Roadmap

Phased, incremental delivery. Each phase ends with working, tested software. The MVP in this repository
delivers **Phase 1 + most of Phase 2 + a Phase 3 dashboard**, with Phases 4–5 scaffolded behind the
canonical schema and adapter interfaces.

**Legend:** ✅ delivered & tested · 🚧 partial/scaffolded · ⏳ planned.

## Delivered hardening (from the review in `recommendations.md`)  ✅
- **Resolution ladder** on every call edge (`syntactic`/`import-traced`/`inherited`/`inferred`/`name-match`/`candidate`) with import binding tables, receiver-aware narrowing, local type inference, and a fan-out cap that records `unresolved_call` facts.
- **Stratified impact** (`resolved`/`candidates`/`unresolved`) + computed **completeness** (`exact`/`partial`/`heuristic`); speculative edges never drive multi-hop traversal.
- **Staleness** (`generation`, `head_sha`, `commits_behind`) and **truncation** flags in envelopes.
- **Secrets:** config files never chunked/embedded; best-effort secret scrub of code chunks.
- **Honest signals:** hash embeddings labeled `lexical-hash`; mixed-model vector store guarded.
- **Dynamic MCP tool availability** based on which edge types the index actually contains.
- Full status matrix: [`recommendations-status.md`](recommendations-status.md).

## Gap Registry — "known unknowns"  ✅ (this repo)
Never drop a resolution failure. Unresolved edge targets become **placeholder (stub) entities**
(`attributes.missing=true`, reserved `__missing__` id segment) so edges stay in the graph and
**self-heal** when the artifact is later indexed; each missing artifact gets a **gap record** naming
it, its expected origin, and every referencing site. Surfaces: `uci gaps` (ranked acquisition
checklist), the `list_index_gaps` MCP tool, a **Gaps** dashboard page (stub nodes rendered dashed in
the graph explorer), and `completeness.gaps` citations in impact packs. An external/stdlib classifier
keeps vendor imports out of the report. Spec: [`next-iteration-gap-registry.md`](next-iteration-gap-registry.md).

## Phase 1 — MVP: graph + vector + CLI  ✅ (this repo)
- Canonical schema (`uci.core`): entities, relationships, IDs, provenance, normalization.
- Ingest (`uci.ingest`): scanner, ignore rules, language detection, content-hash incremental, git metadata.
- Parser (`uci.parser`): Python (`ast`) + JS/TS extraction of files, symbols, imports, calls, references.
- Embeddings (`uci.embeddings`): symbol-aware chunking + provider abstraction (Noop, Local hash-based).
- Graph (`uci.graph`): `GraphStore` interface, `InMemoryGraphStore`, `SQLiteGraphStore`.
- Store (`uci.store`): SQLite `MetadataStore` + `VectorStore` (numpy brute force).
- Retrieval (`uci.retrieval`): hybrid RRF (symbol/keyword/semantic/graph/proximity/churn).
- Analysis (`uci.analysis`): repo overview + architecture/layer inference.
- CLI (`uci.cli`): `init`, `index`, `watch`, `query`, `graph symbol`, `impact`, `serve`, `mcp`.
- Tests: contract tests (in-memory ≡ sqlite), unit tests, tiny fixture repos.

## Phase 2 — MCP + better retrieval  ✅ (this repo, MVP subset)
- MCP server (`uci.mcp`) with `search_code`, `find_symbol`, `get_callers`, `get_callees`,
  `impact_analysis`, `explain_module`, `retrieve_edit_context`, `find_tests_for_symbol`.
- Adaptive fusion + graph expansion + impact packs + edit-context assembly.
- **Resolution ladder:** every call edge is tagged `syntactic`/`import-traced`/`inherited`/
  `name-match`/`candidate` with derived confidence (see `retrieval-strategy.md` §9).
- **Next:** cross-encoder rerank adapter; multi-hop path queries; NL→structured query planner;
  a **retrieval evaluation harness** (labeled query→symbol dataset; report MRR/Recall@k and
  call-graph precision/recall per resolution level) so accuracy claims are measured, not asserted;
  optional LSP/SCIP ingestion to promote edges into the provable (R0–R3) strata.

## Phase 3 — Dashboard  ✅ (this repo, MVP subset)
- FastAPI + server-rendered dashboard: overview, module list, symbol search, symbol detail,
  offline canvas **graph explorer**, impact view, architecture summary, onboarding guide.
- **Structural-honest** by design: every view derives from graph facts; the onboarding guide is a
  dependency-ordered reading path (topological/heuristic — no LLM required).
- **Next:** React/`@xyflow/react` client, persona filtering, guided tours, diff-impact overlay,
  domain/business view (Understand-Anything parity), i18n; and an optional **LLM-enrichment adapter**
  (`uci enrich`) that writes `summary`/`layer`/`domain`/`capability` attributes with
  `extractor="llm:<model>", confidence<1.0` — enrichment as just another extractor over the same graph.

## Phase 4 — Non-semantic relationships  ⏳ (schema-ready, extractors to add)
- Data: SQL/ORM extractors → `Query`/`Table`/`Column`, `READS`/`WRITES`, DTO↔entity `MAPS_TO`.
- Runtime/config: route extractors → `API_ENDPOINT` `HANDLES`; `FEATURE_FLAG` `CONTROLS`; `EMITS` log events.
- Testing: coverage integration → `TESTS`/`COVERS`, `FailedTest → indicates_risk_for → CodeChange`.
- Ownership/evolution: git blame → `AUTHOR`/`TEAM` `OWNS`; churn → `increases_risk_for`; ticket linking.
- Business/domain: `BUSINESS_CAPABILITY`, `USER_FLOW`, `REPORT` inference (heuristic + optional LLM).

## Phase 5 — Legacy modernization  ⏳ (schema-ready, sample fixtures included)
- COBOL parser: `LEGACY_PROGRAM`, `PARAGRAPH`, `COPYBOOK`, `COPYBOOK Field` → `MAPS_TO` DB/API.
- JCL parser: `JCL_JOB` `RUNS` program; step/DD dataset `READS`/`WRITES`.
- CICS/IMS: `TRANSACTION_CODE` `INVOKES` `LEGACY_PROGRAM`/`SCREEN`.
- Modernization: `LEGACY_MODULE → CANDIDATE_FOR_MIGRATION → TargetService` with mapping reports.
- Sample COBOL/JCL/copybook fixtures ship now (`examples/legacy/`) to validate the schema end-to-end.

## Cross-cutting adapter roadmap (behind interfaces, config-selectable)
| Category | local-lite (now) | First upgrade | Later |
| --- | --- | --- | --- |
| Graph | InMemory + SQLite | **Memgraph** | Neo4j |
| Vector | SQLite/numpy | **Qdrant** | LanceDB, pgvector |
| Metadata | SQLite | — | **Postgres** |
| Embeddings | Noop / Local(hash) | **Ollama** | OpenAI, Anthropic, Gemini |
| Text search | Python / SQLite FTS | **ripgrep** | — |
| Parsers | Python, JS/TS | Java, C#, Go, Rust | COBOL/JCL (experimental) |

Deliverables per profile: `docker-compose.local-pro.yml` (Memgraph + Qdrant + Ollama) and a three-profile
`.env.example` ship now; the adapters themselves land in the phases above.

## Definition of done (per phase)
- New capability has contract/unit tests that run in CI without Docker or network.
- Optional-backend tests are marked and skipped when the backend is absent.
- Docs updated (schema, retrieval, mcp-tools as relevant).
- All extracted facts remain traceable to file + line range.
