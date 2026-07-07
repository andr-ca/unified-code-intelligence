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

## Phase 5 — Legacy modernization  🚧 (parsers delivered & eval-scored; deeper extraction ⏳)
- ✅ COBOL parser (`parser/cobol_parser.py`): `LEGACY_PROGRAM` + `COPYBOOK` symbols; literal
  `CALL`/`EXEC CICS XCTL|LINK` → resolved calls; **dynamic** targets (`CALL WS-PGM`,
  `XCTL PROGRAM(var)`) → unresolved sites (honest completeness); `COPY`/`EXEC SQL INCLUDE` →
  copybook dependency edges with external-vs-missing gap classification; `EXEC SQL` →
  `READS`/`WRITES` `DATABASE_TABLE` edges.
- ✅ JCL parser (`parser/jcl_parser.py`): `JCL_JOB` `RUNS` program (`EXEC PGM=`); **`.prc`/`.proc`
  PROC members parsed** (job → proc → program chains resolve instead of gapping);
  `DD DSN=` → `DATASET` `READS`/`WRITES` edges (DISP heuristic, labeled); symbolic
  (`PGM=&VAR`) → dynamic sites.
- ✅ CSD parser (`parser/csd_parser.py`): `TRANSACTION_CODE` `INVOKES` `LEGACY_PROGRAM`;
  `DEFINE FILE` → logical `DATASET` (with physical `DSNAME`); `DEFINE MAPSET` → `SCREEN`.
- ✅ BMS parser (`parser/bms_parser.py`): `DFHMSD`/`DFHMDI` → `SCREEN` entities;
  COBOL `EXEC CICS SEND/RECEIVE MAP` → `USES` edges landing on them.
- ✅ HLASM linkage parser (`parser/hlasm_parser.py`): CSECT/ENTRY → `LEGACY_PROGRAM`;
  `CALL` macro + `V(sym)` → call edges; `EXTRN/WXTRN` → `DEPENDS_ON`; `COPY` → copybook
  edges. COBOL→assembler calls (CardDemo `COBDATFT`/`MVSWAIT`) now resolve instead of
  gapping. Macro expansion stays with the Che4z LSP bridge (⏳).
- ✅ DCLGEN detector (in the COBOL parser): `EXEC SQL DECLARE <table> TABLE` copybooks →
  `MAPS_TO` `DATABASE_TABLE` lineage edges (`maps_to` eval category at 1.0).
- ✅ COBOL depth: VSAM/file access (`SELECT…ASSIGN` + `OPEN` modes, `EXEC CICS READ/WRITE FILE`)
  → `DATASET` `READS`/`WRITES`; `PARAGRAPH` symbols + `PERFORM` → intra-program `CALLS`;
  fixed-format **continuation lines joined** before matching; **literal dataflow**
  (`MOVE 'X' TO var` / `VALUE 'X'`) recovers single-literal dynamic calls at the *inferred*
  (R2) rung — with **taint tracking** (any non-literal MOVE or subscripted table target keeps
  the site honestly unresolved; the CardDemo menu-router pattern).
- ✅ Scored against real repos (`evals/`): **mainframe track 94.7/100** (CardDemo 94.7,
  Bank-of-Z ~93, cash-account 96.6) — every structural + honesty category
  (calls/copybook-impact/jobs/transactions/data-access/maps_to/completeness/gaps) at or near 1.0;
  `queries` (NL retrieval over COBOL) is the remaining sub-0.9 cell even after FTS5.
- ⏳ DDL parsing (CREATE TABLE incl. JCL SYSIN streams), DB2 catalog ingester (SYSPACKDEP),
  column-level DCLGEN mapping, IMS PSB/gen semantics (PCB `PROCOPT` read/write intent),
  PL/I + REXX, Che4z LSP bridge (macro expansion, copybook line mapping).
- ⏳ Modernization: `LEGACY_MODULE → CANDIDATE_FOR_MIGRATION → TargetService` with mapping reports.
- 📘 Strategy: how UCI + AI agents run a modernization program end-to-end —
  [`mainframe-modernization-approaches.md`](mainframe-modernization-approaches.md) (approach
  comparison + recommended pipeline) and
  [`mainframe-modernization-tooling-roadmap.md`](mainframe-modernization-tooling-roadmap.md)
  (Copilot integration, new tools, harness catalog, build order).

## LLM enrichment (optional layer)  ✅ (docs/llm-enrichment.md, docs/agentic-enrichment.md)
- `uci.enrich`: protocol-pluggable client (ollama / openai-compatible / anthropic; stdlib HTTP,
  configured via `UCI_LLM_PROTOCOL/URL/API_KEY/MODEL`); passes: summaries (what/why per module,
  indexed into retrieval), capabilities, dynamic-dispatch candidates (`llm-suggested`, never
  resolved), DCLGEN field dictionaries; on-demand `uci briefing` and `uci ask` (answer-location
  routing: code vs data vs not-in-repo). Model capability benchmarked by `evals/llm_eval.py`. **Bounded agentic tool-loop** for the
  candidates pass (`--agentic`: ≤3 read-only tool calls to pull a variable's cross-file
  definition before proposing) — opt-in, gated by the LLM-eval agentic tasks (small local models
  don't yet clear the bar; harness ready for stronger models).

## Cross-cutting adapter roadmap (behind interfaces, config-selectable)
| Category | local-lite (now) | First upgrade | Later |
| --- | --- | --- | --- |
| Graph | InMemory + SQLite | **Memgraph** | Neo4j |
| Vector | SQLite/numpy | **Qdrant** | LanceDB, pgvector |
| Metadata | SQLite | — | **Postgres** |
| Embeddings | Noop / Local(hash) | **Ollama** | OpenAI, Anthropic, Gemini |
| Text search | **SQLite FTS5 (BM25)** ✅ + token fallback | ripgrep | — |
| Parsers | Python, JS/TS, **COBOL, JCL/PROC, CSD, HLASM, BMS** ✅ | Java, C#, Go, Rust | PL/I, REXX |

Deliverables per profile: `docker-compose.local-pro.yml` (Memgraph + Qdrant + Ollama) and a three-profile
`.env.example` ship now; the adapters themselves land in the phases above.

## Definition of done (per phase)
- New capability has contract/unit tests that run in CI without Docker or network.
- Optional-backend tests are marked and skipped when the backend is absent.
- Docs updated (schema, retrieval, mcp-tools as relevant).
- All extracted facts remain traceable to file + line range.
- **No retrieval/extraction change ships without an eval delta**: run `evals/run_eval.py --baseline evals/reports/baseline.json`; the `supported` track must not regress (>1.0-pt track or >0.05 category), and any movement re-commits `baseline.json` with an explanation (`evals/docs/scoring.md` §5). `mainframe` extractor work is expected to *raise* its track.
