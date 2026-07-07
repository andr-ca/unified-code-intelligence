# The Modernization Factory — Tooling & Harness Roadmap (GitHub Copilot-First)

**Status:** strategy document · 2026-07 · companion to
[`mainframe-modernization-approaches.md`](mainframe-modernization-approaches.md) (which
approach to run — this doc assumes its recommendation, **GAIM**) and [`roadmap.md`](roadmap.md)
(UCI Phase 5, which this doc extends).

This document answers: **what else must we add to this tool — or build as new tools,
including harnesses — to run AI-agent mainframe modernization at industrial scale**, with
**GitHub Copilot as the primary execution agent** (per current direction), while staying
agent-agnostic underneath.

> Copilot features move fast. Facts about Copilot below are current as of early/mid 2026
> and marked where volatile — re-verify against GitHub's docs before committing designs.

---

## 1. TL;DR

Build a **modernization factory** in six planes. UCI already provides the hardest one (the
knowledge plane). The biggest gaps, in priority order:

1. **Copilot integration layer (Workstream A)** — make UCI a first-class MCP citizen in
   every Copilot surface; generate per-repo Copilot configuration (instructions, agents,
   prompts) *from the graph*; and build the **coding-agent factory**: auto-generated issues
   with context packs + Actions-hosted verification gates that Copilot iterates against.
2. **Harnesses (Workstream D)** — golden-master batch replay, online replay, data
   reconciliation, and a semantic equivalence lab. **The factory's throughput ceiling is
   harness capacity, not model quality.** Nothing merges without a harness verdict.
3. **Estate coverage (Workstream B)** — the parsers/analyses UCI still lacks (PL/I, REXX,
   IMS, DB2 catalog, scheduler nets, SMF usage, field-level lineage, CRUD matrix, wave
   planner).
4. **New sibling tools (Workstream C)** — extraction (`zextract`), spec mining
   (`spec-gen`), the orchestrator, test-gen, data migration kit, and a public-benchmark
   eval pack.

Everything obeys the house rules UCI established: deterministic facts with provenance,
honest completeness, adapters over vendors, local-first defaults, **no capability without
an eval**, secrets only via `.env`/Actions secrets with a sanitized `.env.sample` — plus
one factory-specific rule: **the core is target-stack-agnostic**. Contracts, harnesses,
comparators, and the graph never encode the target; stack specifics live only in
pluggable kits (C6 runtime bindings, C10 templates, C12 emitters, C13 scaffolds). The
chosen default kits are **Angular (front-end) + AWS Lambda / ECS (Java or TypeScript)**
— see approaches doc §6.3 for the per-unit-class rationale.

---

## 2. What exists today (inventory) and the delta

| Layer | Have today (this workspace) | Gap for factory scale |
| --- | --- | --- |
| Parsing (legacy) | COBOL (calls, dynamic-call taint, copybooks, SQL, VSAM, paragraphs), JCL+PROC, CICS CSD, BMS, HLASM linkage, DCLGEN→table | PL/I, REXX, Easytrieve, Natural, IMS DBD/PSB/MFS, DB2 DDL+catalog, CLIST, SORT cards, scheduler exports, Endevor/ChangeMan, SMF, MQ defs |
| Graph & honesty | Canonical schema (LEGACY_PROGRAM, COPYBOOK, JCL_JOB, SCREEN, TRANSACTION…), resolution ladder, stratified impact, gap registry w/ self-heal, staleness | Migration-unit objects, wave planner, coverage ledger, cross-index drift diff, field-level lineage |
| Retrieval & explain | Hybrid RRF, impact packs, `uci flow` / `uci cfg` (COBOL+HLASM CFGs!), `explain_module` | NL retrieval over COBOL is the weakest eval cell (queries < 0.9) — domain-tuned chunking/synonyms |
| LLM layer | `uci enrich` (validated facts, confidence-tagged), `briefing`, `ask`, bounded agentic loop, `llm_eval` model procurement | Translation/refactor task evals; agent-task benchmark; prompt/recipe packs |
| LSP/SCIP oracles | Che4z COBOL LSP bridge (verify/discover/complete), SCIP ingest, scripted-LSP CI eval | Macro expansion via LSP, copybook line mapping (roadmap ⏳) |
| Agent interface | MCP server, 14 tools, dynamic availability; CodeRAG MCP + `coderag install` (Claude/Codex/Hermes) | **Copilot-specific everything** (Workstream A); REST hardening for Actions |
| Human interface | Dashboard (graph explorer, flows, gaps, onboarding); Understand-Anything portal pattern | SME rule-validation queue, migration burn-down view |
| Evals | Real-repo mainframe track (CardDemo 94.7 / Bank-of-Z 92.7 / cash-account 96.6), golden datasets, independent miner, baseline gate | Same discipline extended to *migration* (translation correctness, harness fidelity, agent throughput) |
| Extraction/data | — | `zextract`, EBCDIC/copybook-aware data pipeline, profiling, masking |
| Harnesses | eval harness pattern only | H1–H8 below — the core build |

---

## 3. Target architecture: six planes

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1 EXTRACTION      zextract: Endevor/ChangeMan→Git · Zowe CLI · EBCDIC data  │
│                   pipeline · SMF/scheduler exports · profiling/masking      │
├─────────────────────────────────────────────────────────────────────────────┤
│ 2 KNOWLEDGE (UCI) canonical graph · gaps · flows/CFG · enrich · briefings   │
│                   ← the deterministic ground truth every plane reads        │
├─────────────────────────────────────────────────────────────────────────────┤
│ 3 PLANNING        router (per-asset) · wave planner · migration units ·     │
│                   coverage ledger · drift diff                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ 4 EXECUTION       GitHub Copilot coding agent (primary) · Copilot in IDE ·  │
│                   other agents via same MCP · issue factory · context packs │
├─────────────────────────────────────────────────────────────────────────────┤
│ 5 VERIFICATION    H1 batch golden-master · H2 online replay · H3 data       │
│                   reconciliation · H4 equivalence lab · H5 test-gen ·       │
│                   H6 perf/window · H7 factory evals · H8 provenance ledger  │
├─────────────────────────────────────────────────────────────────────────────┤
│ 6 DELIVERY        GitHub: PRs · Actions gates · Copilot code review ·       │
│                   burn-down dashboards · audit/compliance exports           │
└─────────────────────────────────────────────────────────────────────────────┘
```

Contract between planes: **facts down, artifacts up, verdicts sideways.** Agents never
write to the knowledge plane directly; harness verdicts are machine-readable artifacts
agents iterate on; every artifact carries provenance back to graph facts.

---

## 4. Workstream A — GitHub Copilot integration layer (primary)

Copilot is four surfaces, and the factory should exploit all of them: **VS Code / IDE
chat**, the **coding agent** (assign work on github.com, runs in Actions), **Copilot CLI**,
and **Copilot code review**. The unifying mechanism across all four is now **MCP +
instructions files** — Copilot Extensions are deprecated in favor of MCP, so we invest
exclusively in MCP.

### A1. Make UCI a first-class Copilot MCP server

What to build (mostly hardening of `uci mcp`):

- **Streamable HTTP transport** alongside stdio, with `.env`-configured API key — the
  coding agent and github.com chat consume remote MCP servers; stdio only covers the IDE.
  Containerize (`uci mcp --http`) for one-line deploy next to the estate mirror.
- **Copilot-tuned tool surface.** Copilot selects tools by name/description quality and
  benefits from small, purposeful outputs. Add a `copilot` profile that (a) trims to the
  ~10 highest-value tools, (b) enforces response-size budgets (top-N + "ask again with
  cursor" pagination — impact packs can be huge), (c) returns Mermaid for
  `flow_diagram`/`control_flow` so diagrams render directly in Copilot chat and PR
  comments.
- **Two new tools the migration loop needs:**
  `get_migration_unit(id)` → the full context pack (§A3) and
  `get_harness_verdict(unit, run)` → latest structured verdict (§8), so the agent can
  re-read its failure *through MCP* instead of parsing CI logs.
- **Org rollout:** publish to the org's MCP registry/allowlist (enterprise policy
  controls which servers agents may use — get on that list early), plus
  `.vscode/mcp.json` templates in every migration repo.
- `coderag mcp` already ships an `install` command for Claude/Codex/Hermes — add
  **`--target copilot`** writing `.vscode/mcp.json` + repo coding-agent MCP config, and
  mirror the same installer into UCI.

### A2. `uci copilot init` — generate the Copilot configuration *from the graph*

The highest-leverage trick in this whole document: **Copilot's per-repo configuration
files are a compilation target.** UCI knows the estate; emit that knowledge in the exact
files each Copilot surface reads:

| Generated file | Content compiled from the graph |
| --- | --- |
| `.github/copilot-instructions.md` | Estate overview (architecture summary from `uci enrich`), naming conventions, the iron rules ("never assert a caller without `get_callers`; cite `file:line`; unresolved means unresolved"), harness workflow, target-stack standards |
| `.github/instructions/*.instructions.md` (path-scoped via `applyTo`) | Per-area rules: `cobol/**` → dialect notes, copybook conventions, "do not edit, translation source only"; `target/**` → target-kit idioms (Angular / Lambda / ECS per C10), decimal-arithmetic rules (approaches doc §9, runtime-binding mandatory); `jcl/**` → scheduler mapping table |
| `AGENTS.md` | Agent-neutral operating manual (also read by Copilot coding agent + CLI): how to query UCI-MCP, how to run the harness locally, definition of done (verdict green + provenance block in PR) |
| `.github/agents/*.md` (custom agents) | Role presets: `cobol-explainer`, `migration-translator`, `harness-diagnostician`, `jobol-refactorer` — each pinned to the MCP tools + instructions it needs |
| `.github/prompts/*.prompt.md` | Reusable parameterized tasks: "translate paragraph to service method", "explain this abend", "draft characterization tests for `<program>`" |
| Copilot Spaces / knowledge bases *(volatile area)* | Auto-exported estate briefing pack: capability map, per-program summaries, glossary — attached so chat answers estate questions org-wide |

Regenerated on re-index; drift between graph and instructions is itself a CI check.
This is a thin, cheap tool (a rendering pass over existing graph queries) with outsized
effect: **every Copilot seat in the org inherits the estate's ground truth.**

### A3. The coding-agent factory (Copilot as the workforce)

The pattern that scales: **one migration unit = one GitHub issue = one Copilot coding
agent session = one PR = one harness verdict.**

Build the `issue factory` (part of the C7 orchestrator):

1. **Issue generation.** For each unit in the current wave, render an issue containing:
   the goal + route (translate/refactor per the router); the **context pack** — program
   source refs, copybooks, `uci cfg` Mermaid, impact summary with resolution strata,
   data-access table, known gaps ("caller X unresolved — do not invent it"); the
   done-definition (harness green + provenance block). Sized deliberately: the pack is
   *pointers + digests*, the agent pulls detail via MCP.
2. **Environment.** `.github/workflows/copilot-setup-steps.yml` preinstalls the harness
   CLI, comparators, GnuCOBOL (for local legacy replay where dialect allows), and target
   toolchain, so the agent can run verification *inside its session* before pushing.
   Register the UCI MCP server in the repo's coding-agent MCP configuration (tool
   allowlist: read-only graph tools + `get_harness_verdict`). Firewall allowlist: UCI
   endpoint + package registries only. All secrets via Actions secrets, mirrored in
   `.env.sample`.
3. **Gates.** Branch protection requires: build, H1/H2 verdict (JSON artifact + PR check
   summary), H4 rules pass, provenance block present (H8), **Copilot code review** with
   custom coding guidelines auto-derived from the same instruction set (decimal rules,
   banned patterns like naive `double` money math), plus CodeQL/secret scanning on the
   target repo.
4. **The iterate loop.** Failing gate → verdict artifact → agent (session still attached
   to the PR, or re-assigned) reads it via `get_harness_verdict`, patches, pushes;
   repeat with an attempt budget (e.g. 5). Exhausted budget → label
   `needs-human` + `harness-diagnostician` agent posts a root-cause comment. This loop —
   *agent iterating against a deterministic oracle* — is the factory's engine; everything
   else is logistics.
5. **Fleet management.** Waves fan out N units in parallel (Actions concurrency +
   premium-request budget are the real limiters — meter them in H7). Agent HQ / mission
   control (github.com) is the human overview; our orchestrator tracks unit state
   (`generated → assigned → iterating → verdict-green → in-review → merged → cut-over`)
   in the graph itself, so the dashboard burn-down and the delivery state can never
   disagree.

**Agent-agnostic escape hatch:** the unit/issue/context-pack/verdict contract is plain
GitHub + MCP + JSON. Claude Code, Codex, or an in-house runner can work the identical
queue — useful for A/B-ing agents per unit class (H7 measures who wins where; route
accordingly).

### A4. Copilot Q&A over the estate (pre-migration value)

Before any translation, wire UCI-MCP into org-wide Copilot chat: "which jobs touch
CUSTMAST?", "explain transaction CO01 end-to-end", "what's still unresolved in the
payments slice?" answered with citations in the IDE and on github.com. This is Workstream
A's *first shippable milestone* (needs only A1) and converts the maintenance workforce —
including non-mainframe engineers — into people who can safely touch the estate. Measure
with the existing `queries` eval category (the known-weak cell; fixing it is a B-track
item that directly improves this experience).

### A5. Future upgrades: hand off to Copilot app modernization

Design the *output* of migration so GitHub's own modernization agents keep it fresh
(the "primarily from a GitHub Copilot perspective" requirement, applied forward):

The factory core is stack-agnostic (§1); the chosen kits (approaches doc §6.3) are
**Angular + Lambda/ECS with Java or TypeScript** — picked, among other reasons, for
their upgrade-agent story:

- **Java units (ECS/batch):** GitHub Copilot app modernization covers Java upgrades with
  agentic assess→plan→execute→validate loops. Our COBOL→Java output becomes *their*
  input for Java N→N+1 and framework/CVE waves — modernization becomes a continuous
  property, not a one-time event. Emit standard Maven/Gradle builds to keep the handoff
  clean.
- **TypeScript units (Lambda) + Angular front-ends:** no dedicated app-modernization
  agent yet — rely on the mainstream paths agents already handle well: `ng update`
  schematics, Dependabot/npm-audit waves worked by the coding agent, Lambda runtime
  deprecation issues auto-filed by the orchestrator. Same principle, generic tooling.
- Keep the graph running on the target estate (UCI parses TS/Java via
  code-graph-rag-style tree-sitter parsers), so post-migration upgrades get the same
  impact analysis the migration had.
- Emit standard build systems, AGENTS.md, and tests-as-contract — exactly the artifacts
  upgrade agents key on. **Anti-goal:** bespoke runtime frameworks that orphan us from
  the upgrade ecosystem (score route-D transpiler vendors on this too — most emit Java).

### A6. Model & agent routing, measured

Extend `evals/llm_eval.py` from enrichment tasks to **factory tasks**: translation
correctness (per program class), JOBOL-refactor quality, harness-failure diagnosis,
test generation. Score Copilot's selectable models (and non-Copilot agents) per task
class; the router (§3 planning plane) consumes the table. House rule applies: no model
choice without an eval delta. (The existing procurement pattern in
[`../evals/docs/llm-comparison.md`](../evals/docs/llm-comparison.md) is exactly right —
generalize it.)

---

## 5. Workstream B — UCI core extensions for the estate

### B1. Parser/ingest coverage (extends Phase 5 ⏳ list)

Priority-ordered; each lands with eval categories on the mainframe track, per the
definition-of-done in `roadmap.md`:

1. **DB2 DDL + catalog ingest** (`SYSIBM.SYSPACKDEP`, plans/packages) — closes the
   program→table graph authoritatively where source scanning under-resolves; DDL parsing
   incl. JCL SYSIN streams (already ⏳ in roadmap).
2. **Scheduler exports** (Control-M XML, CA-7, TWS/OPC) → `JOB_STREAM` / `PRECEDES` edges.
   The scheduler *is* the batch application's control flow; also the strongest dead-code
   evidence (defined but never scheduled).
3. **SMF/usage ingest** (SMF 30/110 summaries, or Endevor/ChangeMan footprints) →
   `EXECUTES_IN_PROD (last_seen, frequency)` attributes — turns dead-code detection from
   inference into observation, and prioritizes waves by real traffic.
4. **IMS**: DBD/PSB gen (hierarchies, PCB PROCOPT read/write intent — roadmap ⏳), MFS
   for screens; `SEGMENT` entities with parent/child edges (the lineage substrate for H3
   on IMS estates).
5. **PL/I parser** (procedures, `%INCLUDE`, `EXEC SQL/CICS`) — same honesty machinery as
   COBOL; **REXX/CLIST** (call targets, `ADDRESS TSO/ISPEXEC` seams) for the glue layer.
6. **Easytrieve / Natural (+ADABAS DDMs)** where the estate demands; **SORT control
   cards** (DFSORT/SYNCSORT SUM/INCLUDE/OMIT are business logic hiding in JCL);
   **MQ definitions** → `QUEUE` entities (async seams for the strangler).
7. **Endevor/ChangeMan metadata** during extraction → processor groups, element history →
   ownership/churn signals matching the git-side ingest.
8. **Che4z LSP deepening** (roadmap ⏳): macro expansion, copybook line mapping — promotes
   assembler/copybook edges up the resolution ladder.

### B2. Analyses (graph-derived, deterministic)

- **Field-level data lineage** (roadmap ⏳ column-level DCLGEN → complete it, then MOVE-chain
  propagation through copybooks): `CUSTMAST.CM-BALANCE → WS-BAL → REPT-LINE-27` — the
  question every data migration and every comparator tolerance rule asks.
- **CRUD matrix** (program × dataset/table × C/R/U/D) as a first-class report + MCP tool —
  the seam-finder for slicing and the checklist for H3.
- **Dead & duplicate code**: no-in-edge + no-schedule + no-SMF composite score; clone
  detection across members (token/AST hashing) — generated-code families collapse to one
  translation + N parameterizations.
- **Seam scoring**: per candidate slice boundary, count crossing edges by resolution
  level + shared-data pressure → ranked "next best slice" list for the wave planner.
- **Complexity/risk composite** per program (`get_code_metrics` + cyclomatic on the CFG +
  dynamic-call density + copybook fan-in) → router feature vector.
- **Batch-window model**: job-net critical path from scheduler edges + (SMF) runtimes —
  the constraint H6 tests against.
- **Graph drift diff** (`uci diff --since <index-gen>`): what changed on the mainframe
  since the wave was planned — the anti-"migrating a ghost" control (approaches doc §8.10).

### B3. Migration-planning objects in the schema

Make the factory's state first-class graph citizens (schema already anticipates this via
`CANDIDATE_FOR_MIGRATION`): `MIGRATION_UNIT` (members, route, wave, status, context-pack
hash), `WAVE`, `COVERAGE` edges (unit → harness lanes + rates), `EQUIVALENT_TO`
(legacy program ↔ target service, carrying verdict history). The dashboard burn-down,
the issue factory, and the compliance ledger all read these — one source of state.

---

## 6. Workstream C — new sibling tools (this workspace)

| # | Tool | Purpose (MVP scope) | Depends on |
| --- | --- | --- | --- |
| C1 | **zextract** | Estate → Git mirror: Endevor/ChangeMan/PDS via Zowe CLI/FTPS; encoding conversion; layout-aware **data** extraction (copybook-driven EBCDIC→UTF-8/Parquet incl. COMP-3/REDEFINES/ODO — adopt `cb2xml` for layouts); data profiling + masking for test capture; SMF/scheduler export pullers. Feeds `uci gaps` closure. | Zowe; site access |
| C2 | **spec-gen** | Business-rule & spec miner over the graph: per-capability spec docs (Gherkin/decision tables) with **mandatory `file:line` citations**, SME validation queue (approve/correct → writes back as graph attributes), Understand-Anything-style portal for non-engineers. | UCI enrich; B2 lineage |
| C3 | **harness-batch** | H1 runner + comparators (below). | C1 (captures) |
| C4 | **harness-online** | H2 runner: CICS/3270 capture-replay + API contract tests. | C1; B1-CSD/BMS (have) |
| C5 | **data-migrator** | Schema mapping (copybook/DBD → relational DDL), conversion pipelines, CDC config generation (vendor-adapter pattern: Precisely/Qlik/InfoSphere/tcVISION), H3 reconciliation reports. | B2 lineage |
| C6 | **equivalence-lab** | H4: differential micro-testing of semantic traps (decimal/truncation/collation/dates) between legacy semantics and target library; ships the **blessed target-side runtime lib** (Decimal helpers, EBCDIC-order comparators, date shims) that translations must use. | GnuCOBOL (oracle) |
| C7 | **migration-orchestrator** | The control plane: router + wave planner execution, issue factory (§A3), unit state machine in the graph, burn-down UI, budget metering. | B3; A1–A3 |
| C8 | **testgen** | H5: characterization-test generator — paragraph/section-level COBOL tests (arrange via copybook fixtures, assert via H1 comparators), coverage measured against `uci cfg` reachability. | C3, C6 |
| C9 | **modernization-evals** | H7: extends `evals/` to factory tasks — translation-correctness benchmark per program class (seeded from CardDemo/Bank-of-Z + synthetic trap corpus from C6), agent-throughput metrics, model-routing table (§A6). | C3–C6 |
| C10 | **target-kits** | Opinionated templates per the chosen kits (approaches doc §6.3): **Lambda service kit** (TS and Java handlers, API Gateway wiring), **ECS/AWS Batch batch kit** (Java; Spring Batch where its restart/skip semantics pay for themselves) with **Step Functions** job patterns, **Angular workspace kit** (C13 scaffolds land here) — each with the C6 runtime binding wired in; JOBOL-refactor recipe packs (prompt files + custom agents) for route D stage-2. | C6; A2 |
| C11 | **sme-elicit** | Knowledge-elicitation agent for the retiring-SME problem: interviews subject-matter experts *driven by the graph's blind spots* (unresolved dynamics, unexplained capabilities, gap-registry entries, low-confidence enrichments), records answers as provenance-tagged graph facts (`extractor="sme:<person>"`, confidence set by review) and spec-gen inputs. Runs as a chat surface (Copilot Space / dashboard panel) + a question-queue the program manager schedules against people, not the reverse. The only tool in this table with a hard expiry date — build it early or the knowledge walks out the door. | UCI graph; C2 |
| C12 | **jobnet-migrator** | Batch-orchestration conversion: scheduler net + JCL semantics (from B1 items 2, incl. COND logic, restart/checkpoint steps, GDG rotation, calendars) → target orchestrator definitions (primary per §6.3: **Step Functions state machines + EventBridge Scheduler + ECS/AWS Batch task defs**; alternate emitters: Spring Batch configs, Airflow/Dagster DAGs) with an explicit **semantics-parity report** per job (what mapped cleanly, what needs redesign: COND edge cases, checkpoint/restart, window constraints from B2). The JCL *conversion* twin of the JCL *parsing* we already have. | B1-2, B2 window model |
| C13 | **screen-modernizer** | Green-screen UX route: BMS/MFS maps + screen-flow graphs (SCREEN entities + SEND/RECEIVE edges we already extract) → **Angular scaffolds** (accessible reactive form per map, flow-preserving routing; lands in the C10 Angular workspace kit) + a **3270 parity mode** for retraining-free cutover, with H2 replay as the gate. Explicitly *not* pixel translation — field semantics come from copybooks (B2 lineage), validation rules from paragraph logic mined by spec-gen. | BMS ✅, C2, H2 |
| C14 | **ops-parity kit** | Operational cutover tooling: abend-code → alert/exception taxonomy mapping, console/SYSLOG message parity (what ops watches today → SRE dashboards/alerts on target), checkpoint-restart equivalents, cutover **runbook generator** (per slice: preflight checks from the graph, rollback procedure, reconciliation checklist from H3), and an operator-facing "where is my job" view spanning both worlds during coexistence. Modernization fails at 2 a.m. on day 2 without this. | B1-2/3, C7, H3/H6 |

Keep them separate repos/packages with the UCI adapter philosophy — the factory must not
become a monolith that only runs whole.

### Build vs adopt (don't rebuild what exists)

Adopt/integrate: **Zowe** (z/OS access), **Che4z COBOL LSP** (already bridged),
**GnuCOBOL** (local legacy oracle where dialect fits; else capture on-platform via ZD&T /
Wazi-style dev environments), **cb2xml** (copybook layouts), tree-sitter grammars
(target side), CDC vendors (C5 adapters). Vendor transpilers (AWS Transform lineage,
TSRI, CloudFrame…) slot in as **route-D engines inside the same gates** — the harness
judges them exactly like agents. Products to track as competitors-slash-benchmarks:
IBM watsonx Code Assistant for Z, AWS Transform for mainframe, Mechanical Orchard.
**Our defensible position** is the open, local-first, provenance-carrying knowledge plane
plus agent-agnostic verification — not a proprietary translator.

---

## 7. Workstream D — the harness catalog

Shared definitions: a **verdict** is a machine-readable pass/fail + evidence artifact
(§8); every harness runs headless in Actions *and* locally (`copilot-setup-steps.yml`
makes both identical); pass criteria are configured per unit class in versioned policy
files (`.harness/policy.yaml`), never improvised per PR.

| ID | Harness | Oracle | Core mechanics | Pass criterion (default) |
| --- | --- | --- | --- | --- |
| **H1** | **Batch golden-master** | Recorded production/UAT runs | Capture per job: input datasets + DB pre-state → run legacy (on-platform or GnuCOBOL) → outputs, DB deltas, return codes, report files. Replay against target; **copybook-aware field-level diff** (not byte diff): decode both sides via layouts, apply tolerance rules (pinned dates/seeds, rounding-rule table from C6, report carriage-control normalization) | 100% fields equal or covered by an *explicit, reviewed* tolerance rule; RC parity; row-count parity |
| **H2** | **Online replay** | Captured transaction traffic | Capture CICS request/response at COMMAREA/container level (aux trace / bridge exit / 3270 capture where needed); replay as API calls against target service; compare response payloads field-wise + resulting DB deltas (compose with H3); include pseudo-conversational sequences as scripted conversations | Same as H1 per transaction class; conversation-state parity |
| **H3** | **Data reconciliation** | Legacy store during dual-run | Continuous row/field compare across CDC (VSAM/IMS/DB2 ↔ target store) with copybook-aware decoding, keyed sampling for volume, full-scan windows for cutover; drift alarms with lineage-annotated diffs ("this field diverges → written by these 3 programs") | Zero unexplained drift over N business cycles incl. period-end |
| **H4** | **Equivalence lab** | Legacy semantics (GnuCOBOL/on-platform micro-programs) | Property-based + exhaustive-boundary differential tests for the trap table (approaches doc §9): COMP-3 edges, `ON SIZE ERROR`, COMPUTE intermediate precision, EBCDIC collation, date windowing. Generates the **conformance suite the C6 runtime lib must pass**, and per-PR checks that translations *use* the lib (lint: naive `double`/`float` money math banned) | Conformance suite green; zero banned-pattern hits |
| **H5** | **Characterization test-gen** | Current behavior (right or wrong) | Pre-translation: generate paragraph/section-level tests from copybook-typed fixtures + captured slices; coverage measured against `uci cfg` reachability; tests travel with the unit and become the target's regression suite after translation | Configured branch-coverage floor per route (e.g. E: 80% of reachable branches) |
| **H6** | **Performance / batch-window** | SLAs + B2 window model | Volume replay (scaled captures) against target; job-net critical-path simulation with measured target runtimes; online latency SLOs | Window fits with configured headroom (e.g. 30%); SLOs met |
| **H7** | **Factory evals (meta-harness)** | Golden benchmarks + factory telemetry | C9: translation benchmark scores per model/agent; per-unit iteration counts, verdict-flip rates, human-minutes, premium-request cost; flags degradation (model regressions, prompt rot) and feeds the router | Baseline-gated, like `evals/reports/baseline.json` today |
| **H8** | **Provenance & compliance ledger** | The graph + signed verdicts | Per merged unit: source members+SHAs, graph facts consumed (context-pack hash), agent/model/prompt versions, every verdict artifact, reviewer identity → exportable audit pack (SOX/regulator-shaped); implemented as attestations on the `EQUIVALENT_TO` edge | Ledger complete = merge precondition (a PR check like any other) |

**Sequencing insight:** H1+H4 unblock the pilot; H3 unblocks the first *cutover*; H2 can
lag until the first online slice; H5 raises confidence where captures are thin; H6 before
any big-volume cutover; H7/H8 run from day one cheaply (telemetry + ledger discipline are
much harder to retrofit than to start with).

---

## 8. The agent feedback contract (the glue)

One JSON schema binds planes 4↔5 — emitted by every harness, consumed by every agent
(via `get_harness_verdict` MCP tool and as a PR-check artifact).
**Implemented (NOW-8):** [`../../factory-contracts/`](../../factory-contracts/README.md)
— validators, JSON Schemas, CLI, fixtures (incl. a real CBACT04C verdict). Sketch:

```jsonc
{
  "contract": "harness-verdict/v1",
  "unit": "MIGUNIT-CBACT04C",
  "run": { "id": "…", "harness": "H1", "policy": ".harness/policy.yaml@sha", "when": "…" },
  "verdict": "fail",                    // pass | fail | error | waived(rule)
  "summary": { "artifacts": 14, "failed": 2, "fields_compared": 18234, "fields_diverged": 7 },
  "failures": [{
    "artifact": "TRANFILE-OUT",
    "kind": "dataset",                  // dataset | table | report | rc | response
    "record_key": "ACCT=000000123",
    "field": "TRAN-AMT",                // copybook-resolved name, not an offset
    "layout": "CVTRA05Y.cpy:12",        // provenance, as always
    "legacy": "1234.56",
    "candidate": "1234.55",
    "classifier": "rounding",           // rounding | truncation | collation | date | null-semantics | logic | unknown
    "hint": "COMPUTE intermediate precision — see equivalence-lab rule R-017; use Ucid.decimal.multiplyRounded",
    "lineage": ["ORDERPGM.2100-CALC-FEE (order.cbl:412)"]
  }],
  "repro": "harness-batch replay --unit MIGUNIT-CBACT04C --only TRANFILE-OUT",
  "attempt": { "n": 2, "budget": 5 }
}
```

Design rules: failures are **named in copybook terms with lineage**, not byte offsets —
that's what lets a language model act on them; `classifier` + `hint` are produced by
deterministic rules first (C6 taxonomy), LLM diagnosis (`harness-diagnostician` agent)
only annotates on top; verdicts are append-only and signed → they *are* the H8 evidence.

---

## 9. Build order

**Now (0–3 mo) — prove the loop end-to-end on CardDemo** *(already vendored in `evals/demo-repos/`)*:
A1 MCP-for-Copilot (HTTP + copilot profile) · A2 generator MVP (instructions + AGENTS.md +
2 custom agents) · C3-MVP batch harness with copybook-aware comparator (GnuCOBOL as the
legacy oracle for CardDemo) · C6-MVP decimal/collation rule pack + runtime-lib seed ·
A3-MVP issue factory by hand-rolled script + `copilot-setup-steps.yml` · §8 contract v1 ·
H8 ledger discipline from the first PR. **Demo:** one CBACT batch program translated by
the Copilot coding agent, iterating to green on real verdicts, merged with a full audit
block. *(This is the fundable artifact.)*

**Next (3–9 mo) — industrialize:** C7 orchestrator (unit state in graph, wave planner v1
from B2 seam/complexity scores) · B1 items 1–3 (DB2 catalog, scheduler, SMF) · B2 CRUD +
field-lineage v1 + dead/clone reports · C1 zextract v1 (source loop; data-capture v1) ·
C8 testgen v1 · H3 reconciliation v1 · A4 org-wide estate Q&A rollout · C9 eval pack v1 +
A6 routing table · **C11 sme-elicit v1** (question queue from gaps/low-confidence facts —
the expiry-dated tool; graph write-back with `sme:` provenance) · fix the `queries` eval
cell (COBOL-aware chunking/synonym layer).

**Later (9–18 mo) — coverage & scale:** B1 items 4–8 (IMS, PL/I, REXX, Easytrieve/Natural,
MQ, Endevor metadata, LSP macro expansion) · H2 online replay + first online slice ·
H6 perf/window · C5 data-migrator with CDC adapters · C2 spec-gen portal with SME queue ·
C10 target kits hardened · **C12 jobnet-migrator** (after B1-2 scheduler parsing beds in) ·
**C13 screen-modernizer** (with the first online slice, gated by H2) · **C14 ops-parity
kit** (before the first big-volume cutover; runbook generator with C7) · A5 handoff
pattern validated (run Copilot app modernization on our own Java/ECS units; `ng update` +
coding-agent wave on an Angular/TS slice) · multi-agent A/B routing in production waves.

Issue-ready breakdown of the **Now** horizon:
[`mainframe-modernization-backlog.md`](mainframe-modernization-backlog.md).

Dependencies honored throughout: **harness before factory before scale** (companion doc's
iron rule), evals gate every layer, `.env.sample` ships with every new tool.

---

## 10. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Harness capture blocked by site/z/OS access politics | zextract designed for *incremental* capture (start with UAT runs, GDG copies); GnuCOBOL oracle path for dialect-compatible members; gap registry doubles as the formal "what we still need from the platform team" artifact |
| Copilot platform churn (MCP config, agents files, premium pricing) | Volatile bits isolated in A2 generator templates + A1 adapter; AGENTS.md + MCP + plain issues/PRs as the stable core; quarterly re-verify checklist (§Appendix) |
| Verdict gaming (tolerance rules accrete until everything passes) | Tolerance rules are versioned, reviewed policy (never PR-local), counted in H7, and expire — each rule needs an owner + rationale citing an equivalence-lab finding |
| Model regressions / prompt rot degrade the fleet silently | H7 baseline gates on factory KPIs, same discipline as `evals/` today |
| Graph trusted beyond its honesty (agents act on `candidate` edges) | Copilot profile serializes resolution strata explicitly; instructions ban acting on `candidates`/gaps; `list_index_gaps` in every context pack |
| Coexistence half-life (CDC + dual-run fatigue) | Wave planner optimizes for *seam cleanliness first* (B2 scoring) so slices retire fast; burn-down visibility keeps sponsorship alive |
| Secrets sprawl across factory services | House rule enforced by CI: `.env`-only config, sanitized `.env.sample` per tool, Actions secrets in workflows, secret-scanning on all repos (UCI already scrubs chunks/never embeds config) |

## 11. Success metrics (factory KPIs, all measurable from the planes)

**Knowledge:** % estate members indexed · gap count trend · impact completeness p50 ·
`queries` eval score. **Factory:** units/week reaching verdict-green · median agent
iterations per unit · human review-minutes per unit · premium-request cost per unit ·
% units needing `needs-human`. **Quality:** field-divergence rate at first verdict ·
tolerance-rule count (should *fall*) · post-cutover incident rate · H3 drift events.
**Program:** capabilities cut over · programs retired vs converted (retirement ratio is
the honesty metric) · MIPS decommissioned · burn-down slope.

---

## Appendix: Copilot capability checklist (re-verify quarterly)

Assumed in this doc, current as of early/mid 2026: coding agent works issues in
Actions-hosted environments and opens draft PRs; `copilot-setup-steps.yml` customizes that
environment; per-repo MCP server configuration with tool allowlists (org policy/registry
gating); `.github/copilot-instructions.md`, path-scoped `.github/instructions/*.instructions.md`,
`AGENTS.md`, `.github/prompts/*.prompt.md`, custom agents in `.github/agents/`; Copilot
code review with configurable coding guidelines; Copilot CLI with MCP; model picker with
premium-request metering; Copilot Extensions deprecated in favor of MCP; Copilot app
modernization for Java/.NET (agentic upgrade loops); Agent HQ-style mission control for
multi-agent oversight. Any drift here lands in A1/A2 templates, nowhere else.
