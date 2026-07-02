# LSP Refactoring Recommendations — Using Language Servers to Improve Determinism

**Date:** 2026-07-01
**Companion to:** `recommendations.md` (esp. §1 "resolution ladder" and §1.7 "LSP bridge"). This document answers two questions: **(1)** should UCI leverage LSP to harden its determinism promise, and how; **(2)** what does that strategy look like for IBM mainframe technologies — HLASM, COBOL, DB2, JCL, CICS, IMS — which the canonical schema already reserves types for (`LEGACY_PROGRAM`, `COPYBOOK`, `JCL_JOB`, `PARAGRAPH`, `TRANSACTION_CODE`, `SCREEN`).

---

## 1. Verdict first: yes — but as a *fact source*, not a dependency, and scoped differently per ecosystem

- **For modern languages (Python/TS/JS):** yes. Type-aware language servers already compute the exact edges UCI's name-resolution extractors can only guess (`callHierarchy`, `references`, `definition`, `implementation`). Using them as an **optional enrichment pass** promotes speculative edges to provable ones at near-zero algorithmic cost to UCI. This is the single cheapest way to make the "deterministic" claim true in the languages where it is currently weakest.
- **For mainframe languages:** yes for **intra-program** structure (COBOL copybook/paragraph resolution, HLASM macro expansion — where open-source LSP servers are surprisingly strong), but **no for cross-program edges** — there the system of record is *not* a language server, it's the platform's own configuration artifacts (JCL, CICS CSD, IMS gen, DB2 catalog), which are **more deterministic than any LSP**. The mainframe strategy is therefore: *LSP inside the program, system-artifact extractors between programs.*
- **Never as a required dependency.** LSP enrichment must follow UCI's existing adapter philosophy: optional, lazily loaded, gracefully absent. `local-lite` indexes without any language server; enrichment upgrades edge quality when the toolchain exists.

One naming clarification: this is about using LSP for **fact extraction**, not about UCI performing refactorings. UCI's read-only posture (`mcp-tools.md` §4) is correct and should not change — LSP `rename`/`codeAction` capabilities stay out of scope. The refactoring payoff is indirect: agents doing refactors through `retrieve_edit_context` and `impact_analysis` get trustworthy caller/test lists.

---

## 2. How LSP maps onto the resolution ladder

`recommendations.md` §1.2 defines resolution levels R0–R5. LSP enrichment operates on that ladder in three modes:

| Mode | Input | LSP call | Effect on graph |
| --- | --- | --- | --- |
| **Verify** | Existing R4/R5 (`name-match`/`candidate`) edges | `textDocument/definition` at the call site | Edge confirmed → promote to `resolution="lsp-verified"`, confidence 0.95; definition points elsewhere → **prune** the false edge |
| **Discover** | `unresolved_call` facts (the worklist from `recommendations.md` §1.6) | `callHierarchy/incomingCalls` + `outgoingCalls` on the target symbol | New edges the static extractor missed (dynamic dispatch the type checker can see) |
| **Complete** | High-value symbols (public API, high churn) | `references`, `implementation`, `typeHierarchy` | Fill `REFERENCES`, `IMPLEMENTS`, `EXTENDS` edges with type-aware precision |

Design rules that keep this deterministic in spirit:

1. **LSP results are facts with provenance, not truth.** Every enriched edge records `extractor="lsp:<server>@<version>"`, the file content hash it was computed against, and the mode (`verified`/`discovered`). If the server version changes, enrichment is re-runnable and diffable.
2. **Prune-with-evidence, never silently.** When Verify mode removes a candidate edge, write a tombstone attribute on the remaining record of the call site (`pruned_candidates: 3, by: "lsp:pyright"`) so `completeness` reporting can still explain what happened.
3. **Enrichment is idempotent and incremental.** Key the enrichment cache by `(file content_hash, server, server_version)` — unchanged files never get re-queried. This slots directly into the existing content-hash incremental design.
4. **Budgeted, worklist-driven execution.** Never "run LSP over the whole repo." Priority order: (a) unresolved calls into high-fan-in symbols, (b) R5 candidate groups with the largest fan-out (biggest noise reduction per query), (c) symbols appearing in recent churn. A repo-configurable time/query budget makes cost predictable.

### 2.1 Prefer SCIP indexers over live LSP where they exist

For batch indexing, live LSP servers are the awkward option — they're built for interactive editing (warm-up, project config discovery, memory-resident). The **SCIP ecosystem** (`scip-python`, `scip-typescript`, `scip-java`) produces the same precise cross-reference data as a **one-shot batch artifact** designed for exactly UCI's use case. Recommendation:

- **Tier 1:** consume a SCIP index if present (`index.scip` in the repo or produced by `uci enrich --scip`). SCIP symbols map cleanly onto UCI `entity_id`s (both are path+qname schemes); occurrences map onto `CALLS`/`REFERENCES` with `resolution="scip"`.
- **Tier 2:** live LSP bridge for languages without a SCIP indexer, or for incremental verification of a small dirty set (where a full SCIP re-index would be overkill).

This also future-proofs positioning: SCIP interop means every existing Sourcegraph-ecosystem indexer becomes a free UCI fact source.

### 2.2 Adapter architecture

```
uci.enrich/
  base.py          # EdgeSource interface: verify(edges) / discover(worklist) / complete(symbols)
  scip_source.py   # reads .scip protobuf → canonical edges
  lsp_client.py    # minimal stdio JSON-RPC client (stdlib-only, same policy as the MCP server)
  lsp_source.py    # generic LSP EdgeSource: lifecycle, didOpen batching, budget, cache
  servers.py       # registry: language → launch command, config discovery (venv/tsconfig/copybook paths)
```

- `lsp_client.py` needs only `initialize`, `didOpen`, `definition`, `references`, `callHierarchy`, `shutdown` — a few hundred lines over stdio, no third-party SDK, consistent with the MCP transport decision.
- Server configs (paths to binaries, copybook libraries, dialect settings) belong in `Config.settings` via `UCI_LSP_*` env keys — same pattern as the existing backend settings, documented in `.env.example`.
- CLI surface: `uci enrich [--scip PATH | --lsp LANG] [--budget 60s] [--verify-only]`, runnable independently of `uci index` so the base index never blocks on a language server.

---

## 3. Special focus: IBM mainframe technologies

### 3.1 The strategic insight: the mainframe estate is *more* statically analyzable than Python

This deserves to be stated in UCI's docs, because it inverts the usual assumption. In z/OS application portfolios:

- **Invocation is mostly literal.** `CALL 'PGMB'`, `EXEC CICS LINK PROGRAM('PGMB')`, `EXEC PGM=PGMB` in JCL — string literals resolvable at parse time.
- **Data access is declared.** Static SQL is bound into packages; IMS database access is declared in the PSB with `PROCOPT` (read vs update); JCL `DD` statements name every dataset a step touches.
- **Wiring is centralized system metadata.** CICS CSD defines transaction→program; IMS gen defines transaction→PSB; the DB2 catalog records package→table dependencies.

So for exactly the domain UCI's Phase 5 targets (legacy modernization — where impact analysis has the highest business value), **the "deterministic" promise is actually achievable at R0/R1 levels for the majority of edges** — *if* UCI extracts from the right artifacts. The resolution ladder generalizes cleanly:

| Ladder level | COBOL / CICS | HLASM | DB2 | JCL |
| --- | --- | --- | --- | --- |
| R0/R1 provable | `CALL 'literal'`, `PERFORM PARA-NAME`, `COPY MEMBER`, `EXEC CICS LINK PROGRAM('X')` | `COPY member`, macro invocation, `V(EXTSYM)` / `EXTRN` / `ENTRY` / `CSECT` linkage | static `EXEC SQL` table refs; DCLGEN structure↔table | `EXEC PGM=`, `EXEC PROC=`, `DD DSN=` |
| R2 inferred | `MOVE 'PGMB' TO WS-PGM … CALL WS-PGM` (single-assignment dataflow) | `L R15,=V(X) … BALR R14,R15` patterns | `PREPARE` from literal string | symbolic parameters resolved from `SET`/PROC defaults |
| R4/R5 candidate | `CALL WS-PGM` with multiple/unknown assignments; `EXEC CICS START` with variable TRANSID | branch-to-register with untraceable target | dynamic SQL from constructed strings | `&VAR` from external symbol overrides |
| unresolved (recorded) | `CALL` via linkage-passed name | computed branch tables | `EXECUTE IMMEDIATE :HOSTVAR` | started-task/scheduler-supplied symbols |

### 3.2 Available language servers — what exists and what it's good for

| Server | Coverage | License / form | Value to UCI |
| --- | --- | --- | --- |
| **Eclipse Che4z COBOL Language Support** (Broadcom) | Enterprise COBOL incl. CICS/SQL preprocessing awareness, copybook resolution, dialects (IDMS, Datacom) | Open source, Apache-2.0; Java LSP server, runs headless | The workhorse: definition/references across copybooks, `PERFORM` target resolution, copybook path config — exactly the intra-program facts UCI needs |
| **Che4z HLASM Language Support** (Broadcom) | HLASM incl. **conditional assembly evaluation and macro tracing**, `COPY` resolution, branch/symbol navigation | Open source, Apache-2.0; native LSP server | Rare capability: it actually *evaluates* conditional assembly, so macro-generated code paths and symbols become resolvable facts rather than opaque text |
| **IBM Z Open Editor** | COBOL, PL/I, HLASM, JCL, REXX language servers | Free but proprietary (VS Code extension; servers embedded) | Broadest coverage incl. PL/I and JCL; licensing constrains embedding — treat as user-provided toolchain, detected not bundled |
| **IBM Db2 for z/OS Developer Extension** | SQL language features for Db2 | Free, proprietary | Marginal for indexing; UCI's own SQL statement extractor (below) covers the need |

Recommendation: build the mainframe LSP bridge against the **two open-source Che4z servers first** (embeddable, headless, permissive licenses, active maintenance), with Z Open Editor servers as user-supplied alternates through the same `servers.py` registry.

### 3.3 COBOL: what LSP should do vs. what UCI's parser should do

**Use the LSP for (intra-program, hard to reimplement):**
- **Copybook resolution with line mapping.** `COPY MEMBER REPLACING ==X== BY ==Y==` expansion is the COBOL analogue of C preprocessing; Che4z resolves member paths and understands REPLACING. UCI needs this for correct provenance (see below).
- **`PERFORM` / `GO TO` paragraph target resolution** → deterministic intra-program `CALLS`-like edges between `PARAGRAPH` entities (the schema type exists; this is what populates it).
- **Definition/references for data items** across copybooks → `REFERENCES` edges linking working-storage fields to the copybook that declares them.

**Keep in UCI's own extractors (simple, and LSP doesn't expose them as facts):**
- `CALL` statements and their literal/dynamic classification (the ladder in §3.1) → `LEGACY_PROGRAM → CALLS → LEGACY_PROGRAM`.
- `EXEC CICS` command extraction: `LINK`/`XCTL` (control transfer → `INVOKES`), `START` (async transaction), `READ/WRITE FILE` (→ `READS`/`WRITES` on file entities), `SEND MAP`/`RECEIVE MAP` (→ edges to `SCREEN` entities).
- `EXEC SQL` extraction (see §3.5).

**Dual provenance is mandatory.** A fact found in expanded copybook text has *two* homes: the copybook (where the line lives) and the including program (where the behavior lives). Extend provenance attributes with `included_by: <program entity_id>` (or `declared_in: <copybook entity_id>` from the program's perspective). Without this, either the "traceable to file+line" promise breaks (line numbers point into expanded text that matches no file) or impact analysis breaks (change to a copybook doesn't propagate to the 200 programs that COPY it). **Copybook fan-in is the single most valuable impact fact in a COBOL estate** — `COPYBOOK ←COPY— LEGACY_PROGRAM` edges with accurate provenance make `impact_analysis` on a copybook change the killer demo of Phase 5.

### 3.4 HLASM: macro expansion is the whole game

Raw HLASM defeats naive parsing because most of the interesting structure is generated: macros expand to code, conditional assembly (`AIF`/`AGO`/`SETx`) selects paths, and `COPY` pulls in members. Recommendations:

1. **Lean on Che4z HLASM Language Support for the expanded view.** Its conditional-assembly evaluation and macro tracing turn "text that generates a program" into "a program with known symbols" — use `documentSymbol` + `definition`/`references` over it to emit: macro-invocation edges (`LEGACY_PROGRAM →CALLS(macro)→ MACRO` — model macros as `FUNCTION`-kind symbols with `attributes.hlasm_macro=true`), `COPY` edges, and intra-CSECT branch structure if wanted (probably skip; too fine-grained for the graph).
2. **Extract external linkage yourself — it's trivially deterministic.** `CSECT`/`RSECT` (definitions), `ENTRY` (exported symbols), `EXTRN`/`WXTRN` and `V(symbol)` address constants (imports) are line-level patterns that define the **inter-module object graph** exactly as the binder sees it. These are R0-provable `DEPENDS_ON`/`CALLS` edges between assemblies and load modules — no LSP needed, ~100 lines of extractor.
3. **Standard calling convention as an R2 pattern**: `L R15,=V(TARGET)` … `BALR R14,R15` (and the `CALL` macro that generates it) → high-confidence call edge to `TARGET`. Branch-to-register without a traceable `V`-con load → `unresolved_call` with reason `computed-branch`.

### 3.5 DB2: the catalog is a better oracle than any parser

Three complementary fact sources, in order of authority:

1. **The DB2 catalog itself (highest authority, when reachable).** For bound static SQL, `SYSIBM.SYSPACKDEP`/`SYSPLANDEP` record exactly which packages (≈ programs) depend on which tables/views/indexes. If a customer can export these tables (a single SPUFI/unload job), UCI ingests **the platform's own ground truth**: `LEGACY_PROGRAM → READS/WRITES → DATABASE_TABLE` edges with `resolution="catalog"`, confidence 1.0. Recommend a simple CSV/unload ingester — this is the cheapest determinism win in the entire mainframe story.
2. **Source-level `EXEC SQL` extraction (always available).** Parse embedded SQL in COBOL/HLASM/PL-I: `SELECT/INSERT/UPDATE/DELETE/MERGE` table references → `READS`/`WRITES`; `DECLARE CURSOR` → `READS`; host-variable analysis links `QUERY` entities to the data items feeding them. Static `EXEC SQL` is R1-provable; `PREPARE`/`EXECUTE IMMEDIATE` from constructed strings goes to R4/`unresolved` per the ladder.
3. **DCLGEN copybooks → `MAPS_TO`.** DCLGEN output declares a host structure per table, column by column. Detecting DCLGEN-style copybooks (they contain the `EXEC SQL DECLARE TABLE` alongside the structure) yields deterministic `DTO/VARIABLE → MAPS_TO → DATABASE_TABLE/COLUMN` lineage — precisely the data-lineage edges `find_data_lineage` needs to stop returning `[]`, and a differentiator no grep-based tool provides.

### 3.6 JCL, CICS, IMS: system metadata extractors (no LSP required, maximum determinism)

These are parsers over declarative artifacts — recommend building them as ordinary UCI extractors, since they produce the **inter-program backbone** of the legacy graph:

- **JCL**: `JCL_JOB`/step entities; `EXEC PGM=X` → `RUNS` (R0); `EXEC PROC=` with PROC expansion and symbolic-parameter resolution from `SET`/defaults (R2 when resolved, recorded-unresolved otherwise); `DD DSN=` → dataset entities with `READS`/`WRITES` classified from `DISP=` heuristics (`SHR/OLD` input vs `NEW/MOD` output — label as heuristic, confidence 0.8). IBM Z Open Editor's JCL server can validate syntax, but the fact extraction here is simple enough to own.
- **CICS CSD extract** (`DFHCSDUP LIST` output or CSD unload): `TRANSACTION_CODE → INVOKES → LEGACY_PROGRAM` (R0 — this *is* the system's routing table), plus PROGRAM/MAPSET definitions confirming `SCREEN` entities.
- **IMS system definition / PSB / DBD source**: `TRANSACT`/`APPLCTN` macros → transaction→PSB→program edges (R0); PSB `PCB`/`PROCOPT` → program→database `READS`/`WRITES` with declared intent (R0 — PROCOPT literally says `G` get vs `A` all); DBD → segment/field entities if data-level lineage is wanted.
- **BMS map source** (`DFHMSD`/`DFHMDI`/`DFHMDF` macros): `SCREEN` entities with fields; programs' `SEND MAP('X')` link to them.

### 3.7 Phasing recommendation for the mainframe roadmap

Re-sequence Phase 5 by determinism-per-effort, not by language:

1. **JCL + CSD + IMS-gen extractors** (days each, all R0 edges) → the inter-program skeleton: jobs→programs→transactions. Demo: "what runs PGMB and when?"
2. **COBOL `CALL`/`EXEC CICS`/`EXEC SQL` + copybook `COPY` extractor** with the ladder → the intra-estate call and data graph. Demo: copybook-change impact analysis (§3.3).
3. **DB2 catalog ingester + DCLGEN `MAPS_TO`** → data lineage. Demo: "every program that writes TABLE_X, with line numbers."
4. **Che4z LSP bridge (COBOL, then HLASM)** → paragraph-level precision, copybook line mapping, macro expansion.
5. **HLASM linkage extractor** (`CSECT`/`EXTRN`/`V`-cons) → assembler modules join the graph.

Note items 1–3 need **no LSP at all** and deliver most of the modernization value; the LSP bridge is a precision upgrade, not the foundation. This ordering also means each step ships user-visible impact queries, honoring the roadmap's "each phase ends with working software" principle.

---

## 4. Anti-recommendations (what *not* to do)

1. **Don't make any language server a hard dependency of indexing.** A repo must always index to a usable (if less precise) graph with zero toolchain. Enrichment failing = warning + `completeness` reflects it, never a failed index.
2. **Don't run live LSP servers inside the MCP query path.** Latency and lifecycle management belong in the index/enrich phase; query-time answers come from the graph only. (Exception worth considering later: an explicit `verify_live` tool flag for high-stakes agent queries.)
3. **Don't trust LSP output blindly either.** Language servers have bugs and config sensitivities (wrong venv, missing copybook path = confidently wrong answers). Record server + version + config hash in provenance; when LSP and static extraction *disagree*, keep both with their provenance rather than silently overwriting — disagreement is diagnostic signal.
4. **Don't attempt to reimplement conditional-assembly or COBOL dialect parsing in-house.** Che4z has years of investment; the adapter boundary exists precisely so UCI doesn't own that complexity.
5. **Don't use LSP rename/codeAction to make UCI write code.** Read-only posture stays; refactoring safety is delivered through better facts, not through edits.

---

## 5. Priority summary

| # | Recommendation | Determinism payoff | Effort |
| --- | --- | --- | --- |
| 1 | SCIP-first, LSP-second enrichment tier for modern languages (§2.1) | Promotes the bulk of R4/R5 edges in Py/TS to provable | Medium |
| 2 | Worklist-driven Verify/Discover LSP bridge with provenance + caching (§2) | Turns `unresolved_call` facts into edges or pruned noise | Medium |
| 3 | Mainframe system-metadata extractors: JCL, CSD, IMS gen, DB2 catalog (§3.6, §3.5.1) | R0 edges for the highest-value estate; no LSP needed | Small each |
| 4 | COBOL extractor with call/SQL/CICS ladder + dual-provenance copybooks (§3.3) | Copybook impact analysis — the Phase 5 killer feature | Medium |
| 5 | Che4z COBOL + HLASM LSP adapters (§3.2–3.4) | Paragraph/macro-level precision on top of #4 | Medium |
| 6 | HLASM linkage extractor (CSECT/EXTRN/V-cons) (§3.4.2) | Assembler joins the graph at R0 | Small |

The through-line: **LSP is one rung on the evidence ladder, not the ladder itself.** For Python and TypeScript it is the best available oracle and worth a first-class bridge. For the mainframe, the platform's own declarative artifacts out-determine any language server for cross-program facts — and a UCI that ingests JCL, CSD, IMS gen, and the DB2 catalog can make a *stronger* determinism claim about a 40-year-old COBOL estate than it can about a modern Python service. That inversion is worth building, and worth saying out loud in the positioning.
