# Modernization Factory — "Now" Horizon Backlog (issue-ready)

**Status:** execution backlog · 2026-07 · breaks the **Now (0–3 mo)** horizon of
[`mainframe-modernization-tooling-roadmap.md`](mainframe-modernization-tooling-roadmap.md) §9
into copy-paste-ready issues. Approach context:
[`mainframe-modernization-approaches.md`](mainframe-modernization-approaches.md) (GAIM).

**Goal of the horizon (the fundable demo):** one real CardDemo batch program translated by
the GitHub Copilot coding agent, iterating to green against deterministic harness verdicts,
merged with a complete provenance block. Everything below exists to make that demo honest.

**How to use:** each item is one GitHub issue (title = heading). Dependency order is the
listed order, with parallel lanes marked. Sizes: **S** ≤ 2 days · **M** ≤ 1 week ·
**L** 2–3 weeks. `NOW-11` is the integration milestone; when `NOW-1/3/4/9` are done it can
itself be assigned to the Copilot coding agent.

## Decisions

- **D-1 · Target stack — RESOLVED 2026-07-04 (Andrey).** The factory core (contracts,
  harnesses, comparators, graph) stays **stack-agnostic** by rule; the chosen kits:
  **Angular** front-end; backend **AWS Lambda** (online/request-shaped units) or
  **ECS/AWS Batch + Step Functions** (batch units), in **Java or TypeScript** — language
  per unit class, routed by A6 evals (working defaults: batch→Java, Lambda/online→TS).
  Consequences here: NOW-7 ships the **Java binding first** (the pilot units are batch);
  the **TS binding is mandatory before any TS unit merges** (bare JS numbers are IEEE-754
  doubles — the banned pattern); Angular enters with C13/H2, not in this horizon.
- **D-2 · Pilot unit — open.** Default: smoke on `CBACT01C` (sequential read/report —
  trivial semantics), proof on `CBACT04C` (interest calculation — packed-decimal
  arithmetic, multiple files; exercises the §9-trap table for real). Confirm or
  substitute. **Owner: Andrey.**

---

## NOW-1 · `uci mcp --http`: streamable HTTP transport with token auth  *(M)*

**Why:** Copilot coding agent and github.com chat consume remote MCP servers; stdio only
reaches the local IDE. This is the gate for every A-track item.
**Deliverable:** HTTP transport for the existing MCP server; bearer-token auth via
`UCI_MCP_API_KEY` in `.env` (+ `.env.sample` entry); `Dockerfile`/compose service;
graceful degradation to stdio unchanged.
**Acceptance:** MCP Inspector connects over HTTP; unauthenticated requests 401; existing
stdio tests untouched; new contract tests for the transport; README + `mcp-tools.md`
updated.
**Refs:** roadmap doc §A1. **Depends:** —.

## NOW-2 · Copilot tool profile: trimmed surface, size budgets, Mermaid out  *(M)*

**Why:** Copilot selects tools by name/description quality and chokes on oversized
responses; impact packs can be huge.
**Deliverable:** `uci mcp --profile copilot` exposing ~10 tools with rewritten
descriptions; response budgets (top-N + cursor continuation on `impact_analysis`,
`search_code`); `flow_diagram`/`control_flow` return fenced Mermaid.
**Acceptance:** every tool response ≤ configurable byte budget (default 16 KB) on the
CardDemo index; budget/truncation flagged in envelope (reuse existing `truncation`
convention); eval: 10 scripted Copilot-shaped tool calls return actionable JSON.
**Refs:** §A1. **Depends:** NOW-1 (ship together).

## NOW-3 · MCP tools `get_migration_unit` + `get_harness_verdict`  *(M)*

**Why:** the agent must pull its work order and re-read its failure through MCP, not by
scraping CI logs.
**Deliverable:** two read-only tools: unit → context pack (members, copybooks, CFG
Mermaid, impact strata, data-access table, gap list, pack hash); (unit, run?) → latest
`harness-verdict/v1` JSON (NOW-8). Backed by files/DB the orchestrator writes — define the
storage contract now (`.uci/factory/` naming), even while NOW-9 populates it by script.
**Acceptance:** contract tests with fixture packs/verdicts; dynamic availability (absent
when no factory state, per existing capability mechanism); documented in `mcp-tools.md`.
**Refs:** §A1, §A3, §8. **Depends:** NOW-1; NOW-8 (schema).

## NOW-4 · `uci copilot init` — config generator MVP  *(M)*

**Why:** compile estate ground truth into the files every Copilot surface reads (the
highest-leverage cheap trick in the roadmap).
**Deliverable:** command emitting, from the current index: `.github/copilot-instructions.md`
(architecture summary, iron rules, harness workflow), `AGENTS.md` (MCP usage, run-harness
recipe, definition of done), two custom agents (`.github/agents/cobol-explainer.md`,
`migration-translator.md`), one prompt file (`explain-program.prompt.md`), and
`.vscode/mcp.json`. Idempotent; regeneration diff-able; volatile Copilot specifics
isolated in templates (`src/uci/.../templates/copilot/`).
**Acceptance:** generated skeleton on CardDemo index contains (illustrative floor): the
"never assert a caller without `get_callers`; cite `file:line`; `candidates` are not
facts" rules; per-path blocks for `cobol/**` (translation source — read-only) and target
dir; run instructions for the harness CLI. Golden-file tests over a fixture index; docs
page.
**Refs:** §A2. **Depends:** NOW-1/2 (references the served MCP endpoint).

## NOW-5 · harness-batch MVP: golden capture + replay for CardDemo batch  *(L)*

**Why:** the oracle. Nothing merges without it (iron rule, approaches doc Stage 2).
**Deliverable:** new sibling tool `harness-batch`: (a) **capture** — run a CardDemo batch
program under GnuCOBOL with fixture input files, record inputs, outputs, RC into a
versioned golden bundle; (b) **replay** — run candidate (target-stack) implementation on
the same inputs; (c) emit `harness-verdict/v1` (NOW-8) using the NOW-6 comparator; local
CLI + GitHub Action wrapper, identical behavior.
**Acceptance:** *null-migration test* — replaying legacy against legacy scores 100% (the
harness proves itself first); scope explicitly batch-only (CICS programs out — documented);
`CBACT01C` and `CBACT04C` bundles committed as fixtures; runs offline.
**Refs:** §7-H1, approaches doc §7 Stage 2. **Depends:** NOW-8; parallel with NOW-1..4.

## NOW-6 · Copybook-aware field-level comparator  *(L)*

**Why:** byte-diffs are useless across encodings/decimals; failures must be named in
copybook terms so a language model can act on them (§8 design rule).
**Deliverable:** comparator library used by NOW-5: decodes both sides via copybook layouts
(adopt `cb2xml` or minimal in-house PIC/COMP-3/REDEFINES/ODO reader — decision recorded in
the issue), compares field-wise with per-field tolerance rules from `.harness/policy.yaml`,
classifies divergences (`rounding|truncation|collation|date|null-semantics|logic|unknown`),
resolves field → writing paragraphs via the graph (lineage hint).
**Acceptance:** unit tests over crafted trap fixtures (COMP-3 edges, REDEFINES views,
EBCDIC vs ASCII order, low-values); output slots verbatim into `failures[]` of the verdict
schema; false-positive rate 0 on the null-migration test.
**Refs:** §7-H1, §8, approaches doc §9. **Depends:** NOW-8; feeds NOW-5.

## NOW-7 · equivalence-lab v0: trap rule pack + target runtime lib seed  *(M)*

**Why:** translations must *use* blessed semantics helpers, not re-derive decimal math.
**Deliverable:** `equivalence-lab` repo seed: differential test corpus for the first two
trap families (packed-decimal/COMPUTE precision; EBCDIC collation) with GnuCOBOL as
oracle; runtime lib **Java binding** (per **D-1**; the TS binding fast-follows with the
first online/Lambda slice and blocks any TS unit until it exists) providing decimal
helpers + EBCDIC-order comparator, shipped with the conformance suite; lint
rule/banned-pattern list (naive `double`/bare-JS-number money math) consumable by Copilot
code-review guidelines.
**Acceptance:** conformance suite green on the lib; at least one deliberately-naive
implementation fails it (the suite discriminates); rule IDs (`R-xxx`) referenced by
NOW-6 hints.
**Refs:** §6-C6, §7-H4. **Depends:** D-1; parallel lane.

## NOW-8 · `harness-verdict/v1` JSON Schema + `.harness/policy.yaml` format  *(S)* — ✅ SHIPPED 2026-07-04

**Where:** [`../../factory-contracts/`](../../factory-contracts/README.md) — Python
validators (stdlib-only, the executable source of truth) + JSON Schema interop mirrors +
CLI (`factory-contracts validate-verdict|validate-policy`) + fixtures + 27 passing tests.
**Acceptance met:** the roadmap §8 example (placeholders filled) validates verbatim
(`fixtures/verdict-roadmap-example.json`); a *real* CBACT04C verdict fixture is included
(field/layout/lineage from the actual CardDemo index); policy loader rejects rules
missing owner/rationale/expiry and fails expired rules (`--allow-expired` for historical
docs only); YAML and JSON policies both supported (YAML date-object parsing handled).
**Follow-up:** signing envelope is a placeholder; wire as the H8 check matures (NOW-10).

## NOW-9 · Issue factory v0 + `copilot-setup-steps.yml` template  *(M)*

**Why:** the unit→issue→agent→PR loop, hand-rolled before the orchestrator exists.
**Deliverable:** script rendering a migration-unit issue from the graph (goal/route,
context-pack digest + MCP pointers, done-definition) and writing the pack to the NOW-3
storage contract; `copilot-setup-steps.yml` installing harness CLI, GnuCOBOL, target
toolchain; repo MCP config template (UCI server, read-only allowlist) + firewall
allowlist notes; branch-protection checklist doc (verdict check, provenance check,
Copilot code review w/ NOW-7 guidelines).
**Acceptance:** dry-run against CardDemo index yields a complete `CBACT04C` issue a human
agrees is workable *without asking questions* — the target shape already exists as a
hand-rendered reference built from real `uci` output:
[`mainframe-migration-unit-example.md`](mainframe-migration-unit-example.md); setup
workflow runs green in a scratch repo.
**Refs:** §A3. **Depends:** NOW-3/4/8.

## NOW-10 · Provenance ledger v0 (H8 discipline from PR #1)  *(S)*

**Why:** retrofitting audit trails is miserable; starting them is cheap.
**Deliverable:** PR-body provenance block template (source members+SHAs, pack hash,
agent/model, verdict run IDs, reviewer) + a check that fails PRs missing/inconsistent
blocks; append-only ledger file (or graph attestations on `EQUIVALENT_TO` when B3 lands —
format forward-compatible).
**Acceptance:** check red on missing block, green on NOW-11's PR; ledger entry
reconstructs the full chain for one merged unit.
**Refs:** §7-H8. **Depends:** NOW-8.

## NOW-11 · 🏁 Pilot: CBACT01C smoke, then CBACT04C through the full loop  *(L, milestone)*

**Why:** the point of the horizon — evidence over slideware.
**Deliverable:** in a scratch migration repo wired with NOW-1..10: (1) smoke — CBACT01C
issue assigned to **Copilot coding agent**, agent translates to target stack using MCP
context, iterates against harness verdicts to green, merges with provenance; (2) proof —
same for CBACT04C (decimal traps live here); write-up with measured iteration counts,
human-minutes, cost.
**Acceptance:** both PRs merged with verdict-green checks + complete ledger entries; at
least one *real* agent iteration caused by a comparator-caught divergence (if none occurs,
inject a trap fixture to prove the loop catches it); write-up committed to `docs/`.
**Refs:** §9 Now, approaches doc §7 Stage 3. **Depends:** all above.

## NOW-12 · Factory telemetry seed (H7 day-one metrics)  *(S)*

**Why:** the KPIs in roadmap §11 need a baseline from the very first unit.
**Deliverable:** minimal telemetry: per-unit JSONL (attempts, verdict flips, wall-time,
human-review minutes, premium-request count if surfaced) written by NOW-9's loop; tiny
report script.
**Acceptance:** NOW-11 write-up's numbers come from this file, not manual notes.
**Refs:** §7-H7, §11. **Depends:** NOW-9.

---

### Sequencing at a glance

```
NOW-8 (schema) ──► NOW-6 (comparator) ──► NOW-5 (harness) ─┐
   │                                                        ├─► NOW-11 (pilot) ─► NOW-12 report
NOW-1 ─► NOW-2 ─► NOW-3 ─► NOW-4 ─► NOW-9 ─► NOW-10 ───────┘
D-1 ─► NOW-7 (runtime lib) ────────────────────────────────┘   (parallel lane)
```

House rules apply to every item: secrets via `.env` + sanitized `.env.sample`; no
capability without a test/eval; every emitted fact carries provenance.
