# Mainframe Modernization with UCI + AI Agents — The Approach Playbook

**Status:** strategy document · 2026-07 · companion to
[`mainframe-modernization-tooling-roadmap.md`](mainframe-modernization-tooling-roadmap.md)
(what we still need to build) and [`roadmap.md`](roadmap.md) (Phase 5).

This document catalogs **every viable approach** to modernizing/upgrading mainframe
applications using this tool (UCI — Unified Code Intelligence, plus its sibling projects in
this workspace) together with AI coding agents, compares them, and picks a recommended
approach with a concrete, command-level playbook.

---

## 1. TL;DR

There is no single winner among the classic strategies — **the winning approach is a
pipeline** that combines them, with UCI as the deterministic ground-truth layer and AI
agents as the (cheap, parallel, fallible) labor force:

> **Recommended: Graph-Anchored Incremental Modernization (GAIM)** —
> comprehension-first (build the graph, mine the specs), **harness-gated** (no generated
> code merges without behavioral-equivalence evidence), **per-asset routed** (transpile /
> rewrite / wrap / retire decided per program cluster, not per estate), delivered as a
> **strangler fig** (capability-by-capability cutover with data reconciliation), executed
> by **fleets of coding agents** (GitHub Copilot coding agent et al.) that consume UCI
> context packs over MCP and iterate against machine-readable harness verdicts.

Why this wins: LLM translation quality is no longer the bottleneck — **verification and
comprehension are**. UCI attacks exactly those two: a provenance-carrying knowledge graph
that agents cannot hallucinate against (§3), and an eval discipline that generalizes into
migration harnesses (companion doc). Approaches that skip either the graph (blind
translation) or the harness (trust-me rewrites) are catalogued below and rejected with
reasons (§4, §8).

---

## 2. The problem shape

### 2.1 What "the tool" means in this document

| Project (this workspace) | Role in modernization |
| --- | --- |
| **unified-code-intelligence (UCI)** — flagship | Canonical knowledge graph of the estate: COBOL, JCL/PROC, CICS CSD, BMS, HLASM, DCLGEN, copybooks, embedded SQL, VSAM — plus Python/JS/TS for the *target* side. Serves agents (MCP, 14 tools) and humans (dashboard). Honest completeness, gap registry, impact packs, control-flow + business-flow diagrams, LLM enrichment (`uci enrich`, `uci briefing`, `uci ask`), LSP/SCIP edge oracles, eval suite with a real mainframe track. |
| **CodeRAG** | Local-first hybrid (dense+BM25) code search with MCP + a retrieval eval harness; useful as a fast semantic-recall sidecar over huge estates and for target-side repos. |
| **code-graph-rag** | Tree-sitter multi-language graph + MCP; source of parsing ideas and target-language (Java/C#/Go/Rust) graph coverage. |
| **Understand-Anything** | Business-facing interactive knowledge-graph portal; the pattern for giving non-engineers (SMEs, auditors) a window into the same graph. |

Where this doc says "UCI" it means the platform; where a sibling adds something specific it
is named.

### 2.2 What a mainframe estate actually contains

Modernization plans fail when they equate "the estate" with "the COBOL". A typical z/OS
application portfolio includes, and UCI's schema already models most of:

| Asset class | Typical artifacts | UCI today (Phase 5) |
| --- | --- | --- |
| Batch programs | COBOL, PL/I, HLASM, Easytrieve, sort steps | COBOL ✅ HLASM ✅ (linkage) · PL/I ⏳ |
| Online programs | CICS COBOL (pseudo-conversational), IMS TM, BMS/MFS maps | CICS ✅ BMS ✅ · IMS ⏳ |
| Job orchestration | JCL, PROCs, scheduler nets (Control-M/CA-7/TWS) | JCL+PROC ✅ · scheduler exports ⏳ |
| Data definitions | Copybooks, DCLGEN, DB2 DDL, IMS DBD/PSB, VSAM (IDCAMS) | Copybooks ✅ DCLGEN→table ✅ · DDL/catalog/IMS ⏳ |
| Data itself | VSAM/KSDS, DB2 tables, GDGs, flat files, EBCDIC + packed decimal | out of scope for UCI (extraction tooling — see companion doc) |
| Screens/UX | BMS mapsets, 3270 flows | BMS ✅ screens + SEND/RECEIVE edges |
| Glue & ops | REXX, CLIST, FTP/NDM feeds, MQ, RACF, SMF | ⏳ |
| Institutional knowledge | Runbooks, tribal knowledge, Y2K-era comments | `uci enrich` summaries/capabilities partially recover this |

**Rule of thumb from real estates:** 20–40% of members are dead or duplicated, a small
minority of programs carry most business rules, and *data outlives code*. Any approach
that cannot cheaply prove "this is dead / this is a copy / this is the rule" burns its
budget on the wrong 40%.

### 2.3 What AI agents change — and what they don't

Agents change the economics of three formerly-expensive activities:

1. **Reading** — summarizing a 12k-line COBOL program, explaining a paragraph, naming a
   business capability. (UCI: `uci enrich`, `explain_module`, `flow_diagram --narrate`.)
2. **Writing** — translating, refactoring, generating tests, scaffolding services.
3. **Iterating** — reacting to a failing check with a fix attempt, hundreds of times, in
   parallel.

Agents do **not** change:

1. **Ground truth** — an LLM's claim about "who calls this program" is a guess; the graph's
   answer is a fact with a `file:line` citation. Agents need the graph, not vice versa.
2. **Verification cost** — "the Java compiles and looks right" is not equivalence.
   Behavioral verification must be engineered (harnesses), not generated.
3. **Data gravity and cutover risk** — no model migrates a VSAM master file or convinces an
   auditor. Process and tooling do.

Every approach below is scored against this reality: **agent leverage is only as good as
the deterministic scaffolding around it.**

---

## 3. Why UCI is the anchor (and not just another RAG)

Six properties make UCI specifically suited to be the source of truth under agent-driven
modernization — these are implemented today, not aspirational:

1. **Graph over embeddings.** "Which JCL jobs run ORDERPGM?", "which programs use copybook
   ORDREC?", "what breaks if CUSTREC-BALANCE changes?" are *edge traversals*
   (`RUNS`, `DEPENDS_ON`, `impact_analysis`), answered deterministically with citations —
   the questions that gate every migration decision.
2. **Honest resolution.** Call edges carry a resolution ladder (`syntactic` →
   `import-traced` → `inferred` → `name-match` → `candidate`); dynamic COBOL calls
   (`CALL WS-PGM`) stay **unresolved with taint tracking** rather than being guessed.
   Impact packs stratify `resolved / candidates / unresolved` and report `completeness`.
   An agent consuming this knows *what the graph doesn't know* — the single most important
   defense against confident hallucination.
3. **Gap registry.** Missing artifacts (a called program not yet extracted from the
   mainframe, a copybook not in the repo) become stub nodes + ranked acquisition checklist
   (`uci gaps`, `list_index_gaps`). In practice this drives the *extraction* loop with the
   mainframe team: the tool tells you what to go fetch next, and self-heals when it lands.
4. **Provenance on every fact** (`repo · path · line-range · extractor · confidence`).
   Regulated industries (where mainframes live) need an audit trail from every generated
   artifact back to source. LLM-derived facts are labeled `extractor="llm:<model>",
   confidence<1.0` and validated against the index — hallucinated names are dropped.
5. **Two audiences, one graph.** The same graph feeds agents (MCP: `search_code`,
   `find_symbol`, `get_callers/callees`, `impact_analysis`, `control_flow`, `flow_diagram`,
   `retrieve_edit_context`, `find_tests_for_symbol`, `find_data_lineage`,
   `find_config_dependencies`, `get_code_metrics`, `explain_module`, `list_index_gaps`)
   and humans (dashboard: graph explorer, flows panel, gaps panel, onboarding). SME
   validation of agent output happens against the *same* facts the agent saw.
6. **Measured, not asserted.** The eval suite scores extraction against real mainframe
   repos (CardDemo 94.7, Bank-of-Z 92.7, cash-account 96.6) with an independent
   ground-truth miner and a regression gate. This is the cultural DNA that the migration
   harnesses in the companion doc extend: *no capability claims without a scored dataset*.

**The division of labor, in one line:** UCI states facts; agents draft artifacts; harnesses
issue verdicts; humans decide.

---

## 4. The approach catalog

Twelve approaches, exhaustively. Each entry: what it is, how it runs on UCI + agents,
strengths, weaknesses, and a verdict. They are building blocks — §7 composes the winners.

Legend for "UCI leverage": how much of the approach's risk UCI directly removes
(●○○ low → ●●● decisive).

---

### A. Comprehension-first ("document the estate before touching it")

**What.** Reverse-engineer the estate into living documentation before any code change:
inventory, call/job/data graphs, business rules, capability map, screen flows. Output is a
knowledge portal + machine-readable specs, consumed by every later approach.

**How with UCI + agents.**
```bash
uci index ./estate            # COBOL/JCL/CSD/BMS/HLASM/copybooks → canonical graph
uci gaps                      # ranked list of artifacts still to extract from z/OS
uci enrich                    # LLM passes: summaries, capabilities, copybook fields, architecture
uci flow TRN-CO01             # business flow: transaction → programs → data/screens (Mermaid)
uci cfg ORDERPGM.2000-PROCESS # logic inside a routine, optional --narrate
uci serve                     # dashboard for SMEs: flows, graph explorer, onboarding path
```
Agents (Copilot/Claude in chat or batch) then draft per-program briefs and per-capability
specs *grounded* by MCP tools (`explain_module`, `impact_analysis`, `find_data_lineage`),
with every generated paragraph required to cite graph facts. SMEs correct via the
dashboard; corrections re-enter the graph as attributes.

**Strengths.** Cheapest de-risking step; value even if migration never happens (onboarding,
audit, incident response); creates the asset every other approach needs; politically easy.
**Weaknesses.** Delivers no workload movement by itself; can become an endless
documentation program if not time-boxed; LLM business-rule mining needs SME sign-off.
**Risk.** Very low. **Timeline.** Weeks per application, not years.
**UCI leverage.** ●●● — this *is* UCI's core loop.
**Verdict.** Not a strategy on its own; **mandatory Phase 1 of every strategy.**

---

### B. Encapsulate / API-enable ("wrap, don't rewrite")

**What.** Leave programs on z/OS; expose them as APIs (z/OS Connect, CICS web services, MQ
bridges) so new channels stop growing the legacy surface. Often paired with an event feed
(CDC from DB2/VSAM) for read-side modernization.

**How with UCI + agents.** UCI finds the seams: `TRANSACTION_CODE INVOKES PROGRAM` edges
(CSD parser) enumerate the online entry points; COMMAREA/copybook structures
(`find_data_lineage`, enriched copybook fields) define the payloads. Agents generate the
OpenAPI specs, z/OS Connect artifacts, and consumer SDKs from those copybooks; the graph's
`SCREEN`/BMS edges identify which flows are UI-coupled and need conversation redesign.

**Strengths.** Fast time-to-value (months); zero behavioral risk to the core; buys optionality.
**Weaknesses.** MIPS costs stay; the core keeps aging; API sprawl can calcify the legacy
("we can never turn it off now — 40 consumers").
**Risk.** Low. **UCI leverage.** ●●○ (seam discovery, payload mapping).
**Verdict.** Excellent *first move* and coexistence enabler; a dead end as an endpoint.

---

### C. Rehost / emulate ("lift to an emulator, then improve")

**What.** Recompile/re-run the estate on a distributed/cloud emulation stack (Micro Focus /
Rocket Enterprise Server, NTT OpenFrame, etc.). Code stays COBOL/JCL; the iron goes away.

**How with UCI + agents.** UCI's inventory + gap registry is the *readiness audit*: every
`unresolved` call, missing PROC, assembler exit (`HLASM` nodes), and dataset edge is a
rehost blocker to clear. Post-rehost, UCI keeps indexing the same source — the graph is
platform-neutral — so this composes with every later approach. Agents help port the
periphery (JCL→scheduler config, REXX→scripts, exits→services).

**Strengths.** Fastest exit from hardware/licensing; proven vendors; workforce unchanged.
**Weaknesses.** You still own 100% of the COBOL, now on a niche emulator (new lock-in);
per-MIPS savings often disappoint; assembler/exotic utilities are the graveyard.
**Risk.** Medium (cutover is big-bang-ish per LPAR). **UCI leverage.** ●●○ (readiness
audit, exception hunting). **Verdict.** Legitimate *waypoint* when a datacenter exit date
is forced; pair with a committed onward plan or you've just moved the museum.

---

### D. Deterministic transpile + AI idiomatization ("two-stage refactor")

**What.** Stage 1: rule-based converter (AWS Transform/Blu Age lineage, TSRI, CloudFrame,
Astadia…) mechanically produces compilable Java/C# — semantically faithful but
COBOL-shaped ("JOBOL": God classes, `PERFORM`-shaped control flow, exposed packed-decimal
plumbing). Stage 2: **agents refactor the output into idiomatic code**, gated by tests.

**How with UCI + agents.** Index *both* sides. UCI's graph of the original (paragraph
`CALLS`, data edges, CFG via `uci cfg`) becomes the refactoring map for the generated code:
agents ask `control_flow` of the COBOL to understand what a generated method *means*,
`impact_analysis` to know blast radius before restructuring, and the harness (companion
doc H1/H4) to prove each refactor preserved behavior. `MAPS_TO` edges (DCLGEN → tables)
seed the entity-model cleanup.

**Strengths.** Stage 1 is fast, complete, and semantically careful (decades of vendor
engineering on exactly the traps in §9); stage 2 is where agents genuinely excel
(refactoring *with* tests). Auditable: deterministic step + test-gated step.
**Weaknesses.** Vendor cost & lock-in on stage 1; JOBOL left un-refactored is *worse* to
maintain than COBOL; transpilers cover COBOL well but PL/I/assembler/4GLs unevenly; the
generated runtime frameworks are their own dependency.
**Risk.** Medium-low. **UCI leverage.** ●●● on stage 2 (agents need the original's graph
to de-JOBOL safely). **Verdict.** **Strong route for large, rule-dense batch cores** where
1:1 fidelity matters more than architectural change. One of the two engines inside the
recommended pipeline.

---

### E. Direct AI translation, harness-gated ("agent translation factory")

**What.** Agents translate program-by-program (COBOL→Java/C#/Go…) with no deterministic
transpiler — but each unit merges only after passing a **golden-master / characterization
harness** (captured inputs→outputs from the real system replayed against the new code).

**How with UCI + agents.** This is the flow the tooling roadmap industrializes:
1. UCI defines the **migration unit** (program + copybooks + paragraphs + data edges +
   callers/callees) — `retrieve_edit_context` + `impact_analysis` emit the context pack.
2. Harness captures golden data for the unit (batch: input files/DB state → output
   files/DB deltas/return codes; online: request/response pairs).
3. Agent (e.g. Copilot coding agent assigned a generated issue) translates, runs the
   harness locally/in CI, iterates on the machine-readable diff until green.
4. `uci cfg` on the COBOL is attached to the PR as reviewer context; human reviews the
   *behavioral report*, not 5k lines of diff.

**Strengths.** No transpiler license; output is idiomatic from day one; fully parallel
(hundreds of units in flight); every merge carries equivalence evidence; agents keep
getting better — the factory's throughput rises with each model generation.
**Weaknesses.** Harness construction is the real cost (data capture on z/OS, comparators,
EBCDIC/decimal tolerance rules — see §9); coverage gaps = silent behavior loss; long-tail
programs (assembler, giant `ALTER`/`GO TO` spaghetti) defeat direct translation; 1:1
program mapping can reproduce a bad architecture in a new language.
**Risk.** Medium — concentrated in harness quality, which is *engineerable*.
**UCI leverage.** ●●● (unit definition, context packs, dynamic-call honesty, gap-driven
extraction). **Verdict.** The other engine inside the recommended pipeline — **best for
the mid-complexity bulk of the estate**, and the approach that improves fastest over time.

---

### F. Spec-driven rewrite ("behavior reconstruction")

**What.** Don't translate code — **extract the spec, then build the spec.** Agents +
SMEs derive functional specs and acceptance tests from code/graph/data/production logs;
teams (agent-assisted) build a clean implementation, possibly on a different paradigm
(event-driven services, a rules engine, a modern batch framework), validated by the same
acceptance harness + parallel run.

**How with UCI + agents.** `uci enrich` capabilities + `uci flow` per capability give the
candidate spec skeleton; `uci briefing <symbol>` produces migration-readiness briefs;
agents draft Gherkin/decision-table specs with mandatory graph citations; the
`CANDIDATE_FOR_MIGRATION → SERVICE` edges (already in the schema) record the mapping.
Acceptance tests are seeded from the same golden captures as E — the difference is the
oracle is the *spec*, not byte-for-byte output.

**Strengths.** Only approach that sheds accidental complexity (Y2K shims, dead branches,
40-year-old workarounds); best long-term asset; enables true re-architecture.
**Weaknesses.** Highest effort & risk of "second-system" scope creep; silent behaviors that
never made it into the spec (the classic rewrite killer); needs strong product ownership —
scarce for 40-year-old back-office functions.
**Risk.** High if applied broadly; acceptable when scoped to capabilities with living SMEs.
**UCI leverage.** ●●○. **Verdict.** Reserve for the **crown jewels** — high-change-rate,
strategically differentiating capabilities (UCI's churn signal identifies them).

---

### G. Strangler fig by business capability ("incremental carve-out")

**What.** The delivery pattern: pick one capability, stand up its modern implementation
alongside the mainframe, sync data (CDC), route traffic incrementally, dual-run, cut over,
retire the mainframe part; repeat. It's how D/E/F outputs actually *ship*.

**How with UCI + agents.** UCI is the seam-finder and the retirement ledger:
- `uci flow <capability>` + `BUSINESS_CAPABILITY` enrichment propose slice boundaries;
  `impact_analysis` completeness scores tell you which slices are *cleanly* separable
  (few unresolved edges crossing the boundary = safe seam).
- The graph tracks per-slice status via `CANDIDATE_FOR_MIGRATION` / `MAPS_TO` edges — the
  dashboard becomes the program-level migration burn-down.
- After each cutover, re-index: dead paths surface as programs with no remaining
  `RUNS`/`INVOKES` in-edges → retirement candidates (`uci gaps` in reverse).

**Strengths.** Risk is diced into reversible slices; value ships continuously; the org
learns; funding survives leadership changes because progress is visible.
**Weaknesses.** Coexistence tax (CDC, dual-run infra, EBCDIC↔UTF-8 bridging) is real and
lasts years; shared data (one DB2 table hit by 200 programs) resists slicing; needs
routing/facade discipline.
**Risk.** Low-medium, *distributed*. **UCI leverage.** ●●●. **Verdict.** **The delivery
backbone of the recommended approach.** Not optional for estates that can't take downtime.

---

### H. Data-first migration ("move the data, then the logic")

**What.** Modernize the data layer first: VSAM/IMS/flat structures → relational/cloud
store, with CDC keeping both sides in sync; programs follow (or get replaced) afterwards.
Justified when analytics/regulatory pressure on data access leads the business case.

**How with UCI + agents.** UCI's data edges are the map: `READS`/`WRITES` per program
(SQL, VSAM, CICS FILE), DCLGEN `MAPS_TO` lineage, copybook field enrichment → agents draft
target schemas + conversion specs (COMP-3, REDEFINES, ODO — §9) from copybooks; the CRUD
matrix (companion doc, B-track) ranks tables by blast radius; `impact_analysis` on a
dataset node lists every program to re-point.
**Strengths.** Unlocks analytics/AI on day one; shrinks the hardest cutover (data) early;
clarifies true record layouts (forces the REDEFINES reckoning).
**Weaknesses.** Dual-write/CDC complexity; performance surprises (VSAM key access patterns
vs SQL); programs still COBOL — you've modernized the basement while the house stands.
**Risk.** Medium. **UCI leverage.** ●●○ today (field-level lineage is a roadmap item).
**Verdict.** Lead with it **only when the business case is data-led**; otherwise it's
stage 3 of the strangler loop, per slice.

---

### I. In-place modernization on Z ("modernize the practice, keep the platform")

**What.** Accept the mainframe as a durable platform; modernize *how it's engineered*:
source to Git, CI/CD (DBB/Wazi), VS Code + Che4z/Zowe instead of ISPF, dead-code removal,
agent-assisted maintenance (explain/fix/test COBOL in place), API enablement (B), maybe
COBOL→Java-on-Z for zIIP offload.

**How with UCI + agents.** Once the estate is in Git, UCI indexes it continuously and
becomes the daily driver: Copilot in VS Code with UCI-over-MCP answers "who calls this,
what breaks, which JCL runs it" during ordinary maintenance; `uci enrich` docs onboard the
scarce next generation; the LSP bridge (Che4z) hardens edges. Agents do the drudgery:
test generation, dead-code PRs (evidence: no in-edges + scheduler absence), copybook
hygiene.
**Strengths.** Lowest risk of all; immediate developer-experience win; the workforce cliff
(retiring SMEs) is attacked directly; everything here is a prerequisite for any later exit.
**Weaknesses.** Hardware/licensing economics unchanged; "modern mainframe" can become the
politically comfortable way to never decide.
**Risk.** Minimal. **UCI leverage.** ●●●. **Verdict.** Do ~all of it regardless — it's the
substrate (Git + graph + agents) the recommended pipeline assumes. As an *endpoint*, only
right when the platform decision is genuinely "stay".

---

### J. Replace with COTS/SaaS + retire

**What.** For commodity capabilities (GL, payroll, billing…), buy the package, migrate the
data, retire the code. The best modernization is deletion.

**How with UCI + agents.** UCI proves the *shape* of what's being replaced: capability map
→ candidate for replacement; `READS/WRITES` + interface edges enumerate every integration
the package must honor; dead-code analysis shrinks the perceived scope (often decisively —
you don't re-implement what nothing runs); agents draft the data-mapping and integration
specs from copybooks/DCLGEN.
**Strengths.** Eliminates maintenance forever; vendor carries future compliance.
**Weaknesses.** Gap-fit ("we have 400 customizations"); data migration is still yours;
per-seat economics; the estate's *differentiating* parts never fit a package.
**Risk.** Medium (project), low (technical). **UCI leverage.** ●●○.
**Verdict.** Route commodity capabilities here during portfolio triage (§6); never the
whole estate.

---

### K. Big-bang full rewrite / full-estate conversion

**What.** One program: rewrite (or machine-convert) everything, one cutover weekend.

**Verdict up front: rejected** — kept in the catalog because it keeps being proposed.
Failure causes are structural, not executional: multi-year feedback gap (nothing ships
until everything ships), spec drift against a moving mainframe, silent-behavior loss at
scale, cutover risk concentrated in one weekend, and organizational half-life shorter than
the program. AI agents make the *writing* faster, which makes the pile of unverified code
*bigger sooner* — verification and cutover risk are untouched. Every strength claimed for
K is available from G with the risk diced. **UCI leverage** is irrelevant here; no graph
saves a plan whose feedback loop is measured in years.

---

### L. Hybrid portfolio pipeline ("per-asset routing") — the meta-approach

**What.** Treat A–J as a routing table, not rivals: triage every asset cluster through a
decision function (complexity × change-rate × business value × test-ability), route each to
wrap/transpile/translate/rewrite/replace/retire, deliver via strangler slices, verify
everything through one shared harness stack.

**How with UCI + agents.** The routing function's inputs are exactly UCI outputs:
`get_code_metrics` (size/complexity), churn (git ingest), `impact_analysis` fan-in/out +
completeness, capability tags (enrich), dead-code evidence, gap density (extraction risk).
The companion doc's *wave planner* (C-track tool) turns these into machine-generated
migration waves.
**Strengths.** Fits reality (estates are heterogeneous); optimizes spend; every asset gets
the cheapest sufficient treatment.
**Weaknesses.** Demands orchestration discipline and a control plane (that's the factory in
the companion doc); mixed target styles need governance.
**Risk.** Managed. **UCI leverage.** ●●●.
**Verdict.** **This is the recommended approach's skeleton** — §7 fills in the stages.

---

## 5. Comparison matrix

Scales: ◐ low/poor · ● medium · ●● high · ●●● best-in-class. "Agent leverage" = how much
AI agents accelerate it; "UCI leverage" = how much this tool de-risks it.

| # | Approach | Risk | Time-to-first-value | Total cost | End-state quality | Agent leverage | UCI leverage | Reversible? | Kills MIPS? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | Comprehension-first | ◐ | ●●● (weeks) | ◐ | n/a (enabler) | ●●● | ●●● | n/a | no |
| B | Encapsulate/API | ◐ | ●●● | ◐ | ● | ●● | ●● | yes | no |
| C | Rehost/emulate | ● | ●● | ●● | ◐ | ● | ●● | hard | yes |
| D | Transpile + AI refactor | ● | ●● | ●● | ●● | ●● | ●●● | per-unit | yes |
| E | AI translation + harness | ● | ●● | ● | ●●● | ●●● | ●●● | per-unit | yes |
| F | Spec-driven rewrite | ●● | ◐ | ●●● | ●●● | ●● | ●● | per-capability | yes |
| G | Strangler fig delivery | ◐–● | ●● | ●● | ●● | ●● | ●●● | per-slice | eventually |
| H | Data-first | ● | ●● | ●● | ●● (data) | ●● | ●● | dual-run | partially |
| I | In-place on Z | ◐ | ●●● | ◐ | ● | ●● | ●●● | yes | no |
| J | Replace/retire | ● | ● | ●● | ●●● (deleted code) | ● | ●● | contract | yes |
| K | Big-bang | ●●● | ◐ (years) | ●●● | ●? (unverifiable) | ● | ◐ | no | one day, maybe |
| L | Hybrid pipeline | ◐–● | ●● | optimized | ●●–●●● | ●●● | ●●● | per-unit | progressively |

Reading of the matrix: **A and I are unconditional** (do always). **K is unconditionally
out.** B, C, H, J are *situational moves*. D and E are the two *conversion engines*; F is
the *premium engine* for crown jewels; G is the *delivery pattern*; **L composes them** —
which is §7.

---

## 6. Decision framework

### 6.1 Estate-level posture (pick once)

1. **Is the platform decision "stay on Z" (regulatory, latency, sunk zIIP investment)?**
   → I + B + A. Stop here; revisit yearly. Everything below assumes an exit mandate.
2. **Is there a forced exit date < ~2.5 years (datacenter close, license cliff)?**
   → C (rehost) as the waypoint, with A running concurrently, then continue below from the
   emulator (UCI indexes the same source either way).
3. **Otherwise (strategic exit, multi-year window):** GAIM (§7), no rehost detour unless
   hardware economics force it.

### 6.2 Per-asset routing (run continuously, from the graph)

For each program cluster (UCI: connected subgraph around a capability), score:

| Signal (UCI source) | Route toward |
| --- | --- |
| No in-edges (`RUNS`/`INVOKES`/`CALLS`), absent from scheduler exports | **Retire** (J-lite): archive, don't convert |
| Duplicate/clone of already-migrated logic (similarity — roadmap B-track) | **Retire/merge** |
| Commodity capability tag (enrich) with viable package | **Replace (J)** |
| High complexity (`get_code_metrics`) + low churn + batch + rule-dense | **Transpile+refactor (D)** |
| Low/medium complexity, clean seams (high impact completeness), any style | **AI translate (E)** |
| High churn + differentiating capability + living SMEs | **Spec-driven rewrite (F)** |
| Deep assembler/exotic (HLASM nodes, unresolvable dynamics) | **Wrap (B) now, expert-assisted E later; last wave** |
| Shared utility copybooks / date routines | **Library-ize once** (shared target library, not N translations) |

Every route lands in the same strangler-fig delivery loop (G) and the same harness gates.
The routing is *re-evaluated per wave* — evidence from wave N (harness pass rates, agent
iteration counts per unit) retunes the router for wave N+1. That feedback loop is the
"factory learns" property that makes GAIM improve over time.

### 6.3 Target-stack note

The pipeline is deliberately **target-agnostic**: the graph, migration units, harness
captures, verdict contract, and comparators operate on *behavior and data* (datasets,
fields, transactions), so none of them change if the target changes. Stack specifics are
confined to pluggable target kits (companion doc C10) and equivalence-lab runtime
bindings (C6).

The chosen default for this program: **Angular** for screen-replacement front-ends, and
for the backend **AWS Lambda** (request-shaped work) or **ECS** (long-running work) in
**Java or TypeScript**, per unit class:

- **Online/CICS transactions → Lambda-shaped handlers.** Pseudo-conversational CICS is
  already stateless request/response with externalized state (COMMAREA/containers) — it
  maps more naturally onto Lambda handlers than onto a stateful app server.
- **Batch jobs → ECS (Fargate)/AWS Batch tasks orchestrated by Step Functions** (the
  companion doc's C12 primary target); Lambda's 15-minute ceiling rules it out for heavy
  steps.
- **Java vs TypeScript per unit class**, decided by the router on evals (companion doc
  A6), not dogma: both are top-tier for Copilot; Java brings decimal-arithmetic maturity
  (BigDecimal) for rule-dense batch, TS keeps one language across Angular + Lambda.
  Either way the blessed runtime binding (C6) is mandatory — in TS especially, since
  bare JS numbers are IEEE-754 doubles, exactly §9's banned pattern.
- Modernization doesn't end at the first conversion: Java units hand off to **GitHub
  Copilot app modernization** for future upgrade cycles; Angular/TS units ride the
  mainstream toolchain agents handle well (`ng update`, Dependabot + coding agent).

---

## 7. The recommended approach: GAIM (Graph-Anchored Incremental Modernization)

Composition: **A (always) → per-asset routing (L) over engines D/E/F + moves B/H/J →
delivered as G → on substrate I → gated by harnesses**. Seven stages; each has entry/exit
criteria, UCI mechanics, and the agent's role. Stages overlap after 0–2 (it's a pipeline,
not a waterfall).

### Stage 0 — Substrate (week 0+, continuous)
Source to Git (Endevor/ChangeMan sync), `uci index`, dashboards up, MCP wired into the
IDE and Copilot (companion doc A-track). **Exit:** every developer and agent answers
estate questions from the graph, not from folklore.

### Stage 1 — Inventory & triage
`uci index` the full estate; drive `uci gaps` to (near-)zero with the extraction loop —
**the gap registry is the extraction work-queue**; `uci enrich` for capabilities;
dead/clone analysis. **Exit:** portfolio map with per-cluster routing decision (§6.2)
and a wave plan. Typical output: 20–40% routed to *retire* before anyone converts anything
— the highest-ROI stage of the whole program.

### Stage 2 — Harness before conversion (the iron rule)
Stand up golden-master capture + comparators + data reconciliation (companion doc H1–H4)
on the **pilot capability**. Characterization coverage is measured against the graph
(paragraph/branch reachability from `uci cfg`). **Exit:** replaying *yesterday's
production* through the *unchanged legacy* scores 100% — the harness proves itself on the
null migration first. No unit enters Stage 3 without its harness lane.

### Stage 3 — Pilot slice (one capability, end-to-end)
One mid-complexity, well-seamed capability (the graph nominates it: high completeness, low
gap density, moderate fan-in). Run **both engines** on it — D on its rule-dense batch
core, E on its straightforward programs — measure agent iteration counts, harness pass
rates, human review minutes per unit. **Exit:** capability dual-running in production
with reconciliation green ≥ 1 business cycle (incl. month-end), and a measured cost model
per program class → this calibrates the router and the business case with *evidence*.

### Stage 4 — Factory scale-out
The orchestrator (companion doc C-track) turns the wave plan into work: per-unit issues
with UCI context packs → **Copilot coding agents** translate/refactor in parallel →
Actions run the harness → agents iterate on machine-readable verdicts → humans review
behavioral reports → merge advances the burn-down on the dashboard. Dozens of units in
flight; throughput limited by harness capacity and SME review, *by design*.

### Stage 5 — Slice cutovers (repeating)
Per capability: data sync (H per slice) → shadow/dual-run → incremental traffic shift →
legacy path frozen → reconciliation window → retire programs (graph confirms: in-edges
gone). Rollback = route back; nothing is deleted until N cycles clean.

### Stage 6 — Retirement & compounding
Re-index after every cutover; newly-dead code surfaces automatically; the estate graph
shrinks while the target-side graph (UCI parses the Java/TS too) grows — one continuous
map through the entire transition, which is UCI's quiet superpower: **the graph spans both
worlds during the years they coexist.** End state: mainframe workloads gone or reduced to
the deliberate residue (I-posture for whatever stays); target estate enrolled in ordinary
agent-assisted maintenance and Copilot app-modernization upgrade cycles.

### Worked micro-example (CardDemo, already in `evals/demo-repos/`)
Stage 1: `uci index evals/demo-repos/carddemo` → graph of ~transactions (CSD), jobs,
programs, BMS screens; `uci flow` on the card-account-update capability. Stage 2: golden
capture = CardDemo's batch jobs run on sample data (inputs + VSAM/DB2 state → outputs).
Stage 3: route `CBACT*` batch programs through E (agent translation to the batch target
kit — Java on ECS/AWS Batch under Step Functions, per §6.3) with the account-file
comparator as the gate; the menu-router with its taint-tracked dynamic `CALL` stays
honest-unresolved → routed to expert-assisted lane. This is exactly the demo
to build first — see companion doc, "Now" horizon.

### Why GAIM beats each pure alternative
- vs **D alone:** no vendor lock-in for the whole estate; idiomatic output where cheap.
- vs **E alone:** rule-dense monsters get deterministic fidelity instead of heroic prompts.
- vs **F alone:** rewrite premium spent only on crown jewels.
- vs **C alone:** ends somewhere; emulator becomes a waypoint at most.
- vs **K:** continuous shipping, continuous verification, reversible steps.
- vs "just use Copilot on the COBOL": without the graph, agents guess dependencies;
  without harnesses, nobody can merge what they produce. GAIM is precisely the scaffolding
  that turns raw agent capability into an auditable production process.

---

## 8. Anti-patterns (each has burned a real program)

1. **Translation without an oracle.** Merging LLM-converted code on "compiles + LGTM".
   The failure is silent and surfaces at month-end. Harness first — iron rule.
2. **Similarity ≠ equivalence.** Reviewing generated Java side-by-side with COBOL and
   nodding. Only replayed behavior counts.
3. **1:1 paragraph transplantation.** Faithfully reproducing `PERFORM`-shaped control flow
   as 400 static methods — you've bought JOBOL without the transpiler's rigor. Idiomatize
   behind the harness (D-stage-2 discipline) or don't bother leaving COBOL.
4. **Ignoring data semantics** (§9). EBCDIC collation, COMP-3, REDEFINES: the top source
   of "passed all tests, failed in production".
5. **Migrating dead code.** Converting before triage inflates scope 25–40%. Retire first.
6. **Boiling the documentation ocean** (A without a time-box) — comprehension is a stage,
   not a program.
7. **Skipping the scheduler and ops.** The JCL *and its scheduler net* is the application.
   A perfect program translation without restart/checkpoint/window semantics fails its
   first abend.
8. **One golden agent.** Serial artisanal translation by the best engineer + chat. The
   wins come from the *factory* (parallel agents + gates), not the artisan.
9. **Trusting the graph blindly either.** UCI reports `completeness` and gaps for a
   reason — a slice with 60% resolution isn't ready to route; go extract what's missing.
10. **Freezing the mainframe.** Business change continues during migration; without
    re-index + drift detection on every wave, the target chases a ghost. (The graph diff
    between waves *is* the drift report.)

---

## 9. Appendix: semantic traps that define the harness bar

These are why "it compiles" means nothing and why the companion doc's equivalence-lab
harness (H4) exists. Each is a class of silent divergence between COBOL/z-semantics and
naive target-language code:

| Trap | Divergence |
| --- | --- |
| **EBCDIC vs Unicode collation** | Sort order differs (in EBCDIC, lowercase < uppercase < digits); every `SORT`, `ORDER BY` on migrated data, and key comparison can reorder |
| **Packed decimal (COMP-3) & zoned** | Fixed-point decimal arithmetic vs binary floats/naive `double`; use decimal types + explicit scale everywhere |
| **COBOL intermediate precision** | `COMPUTE` intermediate results have defined COBOL precision/rounding; Java `BigDecimal` defaults differ — per-operation scale rules needed |
| **Truncation & `ON SIZE ERROR`** | Silent digit truncation on MOVE to smaller PIC; target must reproduce or justify |
| **REDEFINES / ODO** | Same bytes, multiple layouts; `OCCURS DEPENDING ON` variable records — schema mapping must be byte-faithful before it can be logical |
| **Low-values/high-values/spaces vs NULL** | Three distinct "empty" semantics collapse into `null` if unmodeled |
| **Date arithmetic** | Julian dates, century windowing (Y2K shims still active), `ACCEPT FROM DATE` |
| **Batch determinism seams** | `CURRENT-DATE`, sequence numbers, GDG generations — harness must pin them to replay |
| **File semantics** | VSAM key access, GDG rotation, DISP=MOD append, RECFM/carriage-control characters in reports |
| **Abend/restart semantics** | `COND` codes, checkpoint/restart, implicit rollback scope — the ops contract of every job |

---

## 10. Pointers

- **What we still need to build to run GAIM at scale** — parsers, harnesses, the Copilot
  factory, new tools, build order: [`mainframe-modernization-tooling-roadmap.md`](mainframe-modernization-tooling-roadmap.md).
- UCI Phase-5 status and eval scores: [`roadmap.md`](roadmap.md), [`../evals/README.md`](../evals/README.md).
- Schema for legacy entities/relations: [`canonical-schema.md`](canonical-schema.md) ·
  MCP contracts: [`mcp-tools.md`](mcp-tools.md) · flows/CFG: [`control-flow.md`](control-flow.md) ·
  enrichment: [`llm-enrichment.md`](llm-enrichment.md).

*Glossary (abbrev.):* **JOBOL** — mechanically converted Java that preserves COBOL shape ·
**golden master** — captured real input/output pairs used as a behavioral oracle ·
**characterization test** — a test asserting current behavior (right or wrong) to detect change ·
**dual-run** — legacy and new implementations processing the same traffic with output reconciliation ·
**strangler fig** — incremental replacement pattern where the new system grows around the old until the old is retired ·
**CDC** — change data capture, log-based data replication keeping two stores in sync.
