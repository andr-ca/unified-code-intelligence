# Recommendations — Closing the Gap Between UCI's Promises and What It Can Deliver

**Date:** 2026-07-01
**Companion to:** `feedback.md` (concept review). This document is the constructive half: concrete changes — design, pipeline, contract, and documentation — that give UCI a realistic chance of meeting its declared promises, with the deepest treatment reserved for the biggest overpromise: **determinism**.

---

## 0. The promises being audited

From `docs/`, UCI declares:

| # | Promise | Where declared |
| --- | --- | --- |
| P1 | Structural answers are "deterministic, explainable, and traceable" — *who calls this, what breaks, which tests cover it* | `architecture.md` §6, `retrieval-strategy.md` §8 |
| P2 | "Complete: finds relevant-but-dissimilar code (a caller need not look like the callee)" | `retrieval-strategy.md` §8 |
| P3 | "Everything is traceable — each node/edge carries provenance" | `architecture.md` §1.4 |
| P4 | "Incremental by default — content-hash change detection re-indexes only what changed" | `architecture.md` §1.7 |
| P5 | "Explainability over flash — results say why and what to query next" | `architecture.md` §1.8 |
| P6 | "No secrets in output … values are redacted" | `mcp-tools.md` §4 |
| P7 | Retrieval "degrades gracefully" and hybrid fusion "beats embedding-only retrieval" | `retrieval-strategy.md` §7–8 |
| P8 | One graph serves agents *and* humans (dashboard that teaches) | `architecture.md` §1.3 |

The recommendations below are grouped by promise. Sections 1–3 (determinism) are the core of this document.

---

## 1. P1/P2 — Determinism & completeness: make the call graph honest, then make it good

### 1.1 The root problem, restated precisely

UCI's graph queries *are* deterministic — over whatever facts were extracted. The overpromise is upstream: **fact extraction for `CALLS`/`REFERENCES` in Python and JS is name-based and heuristic**, so:

- `obj.calculate(cart)` links to *every* known `calculate` (or the wrong one, or none);
- dynamic dispatch, DI, callbacks, decorators, `getattr`, framework routing produce **missing edges**;
- common method names (`get`, `run`, `save`, `process`) produce **false edges** with huge fan-out.

`impact_analysis` inherits both error classes while presenting results with confident IDs and scores. **You cannot fully fix this without a type system — but you can (a) stratify edges by how they were resolved, (b) push as many edges as possible into the provably-correct stratum, and (c) never present the two strata as the same thing.** That combination is achievable and is what "deterministic" should be redefined to mean.

### 1.2 Introduce a resolution ladder (the single highest-leverage change)

Every `CALLS`/`REFERENCES`/`EXTENDS`/`IMPLEMENTS` edge gets a mandatory `resolution` attribute assigned by the normalizer, from a fixed ladder:

| Level | `resolution` | How the edge was derived | Determinism class |
| --- | --- | --- | --- |
| R0 | `syntactic` | Same-scope definition: call to a name defined in the same module/class; `self.method()` / `cls.method()` within the defining class | **Provable** |
| R1 | `import-traced` | Callee name explicitly imported (`from pricing.rules import DiscountRule` → `DiscountRule().apply` links into `pricing.rules`) — follow the import edge, not the global namespace | **Provable** (modulo re-exports) |
| R2 | `inferred` | Lightweight local type inference: assignments from constructors (`calc = PricingCalculator(); calc.calculate()`), annotated parameters/returns (`def f(c: PricingCalculator)`), dataclass/attr fields with annotations | **High confidence** |
| R3 | `inherited` | Method resolved through the `EXTENDS` chain (call on subclass, method defined on base) | **High confidence** |
| R4 | `name-match` | Global unique name match: exactly one symbol in the repo has this name | **Plausible** |
| R5 | `candidate` | Ambiguous name match: N>1 symbols share the name; edges emitted to each, `fan_out=N` recorded | **Speculative** |
| — | *(dropped)* | Names above a fan-out cap (see §1.4) or matching nothing | not an edge |

Rules that make this work:

1. **Confidence is derived from the ladder, not invented**: R0/R1 → 1.0, R2/R3 → 0.9, R4 → 0.6, R5 → `1/N` capped at 0.4. One documented function; no per-extractor folklore.
2. **The ladder is language-generic; rungs are language-specific.** Python gets R0–R5 from `ast` (annotations are free precision — use them). JS starts with R0/R1/R4/R5; TS annotations later move mass into R2.
3. **Store the evidence**: `attributes = {resolution: "import-traced", via: "import pricing.rules L3", fan_out: 1}`. This is what makes the `reason` field in query results truthful rather than decorative.

**Why this meets the promise:** after this change, the sentence in the docs becomes defensible — *"R0–R1 edges are exact; R2–R3 are type-derived; R4–R5 are labeled candidates"* — and every downstream surface can filter or stratify. Determinism becomes a property you can point at per-edge instead of a marketing adjective.

### 1.3 Make import resolution the backbone (it is the deterministic asset you already have)

Python imports are essentially static and the parser already extracts them with `resolve_relative_module`. Invest here first, because **every improvement to import resolution promotes R5 edges to R1**:

- Build a repo-wide **module registry** (module qname → FILE/MODULE entity) during normalization; resolve `ParsedImport.module` against it before falling back to "external".
- Track **imported-name bindings per module**: `from x import y as z` means `z(...)` in this module resolves into `x.y` — *not* into the global name pool. This one table converts a large fraction of calls from R5 to R1.
- Handle **re-exports** (`__init__.py` doing `from .impl import Thing`) by following one extra hop; cap at 2 hops and record `via`.
- For JS: resolve relative specifiers (`./`, `../`) against the file tree including `index.*` conventions; treat bare specifiers as external. Named imports give the same binding table as Python.

### 1.4 Contain the damage from name matching

For the residue that stays at R4/R5:

- **Fan-out cap with a stoplist**: if a method name matches more than K symbols (suggest K=5), emit *no* candidate edges and instead record an `unresolved_call` fact (see §1.6). A `CALLS` edge to 40 different `.get()` implementations is noise wearing a graph costume — it destroys both impact precision and graph-expansion retrieval.
- **Receiver-aware narrowing before giving up**: the parser already captures `receiver`. `self.x()` → search only the class hierarchy; `module_alias.x()` → search only that module; `SomeClass.x()` → that class. Cheap, big precision win.
- **Never let R5 edges drive multi-hop traversal.** Depth-2 BFS over candidate edges compounds error geometrically (0.4 × 0.4 confidence paths presented as "callers of callers"). Rule: graph expansion and `depth>1` traversals follow R0–R3 only; R4/R5 appear at depth 1, clearly labeled.

### 1.5 Split the impact pack into strata the agent can act on

Change the `impact_analysis` (and `get_callers`) contract from one flat list to:

```jsonc
{
  "callers": {
    "resolved":   [ /* R0–R3 hits — safe to treat as the blast radius */ ],
    "candidates": [ /* R4–R5 hits — "possibly affected", with fan_out */ ],
    "unresolved": { "count": 3, "names": ["run", "handle"],
                    "note": "3 dynamic call sites could not be attributed" }
  },
  "completeness": {
    "level": "partial",           // "exact" | "partial" | "heuristic"
    "reasons": ["2 dynamic dispatch sites in this file", "1 decorator wrapper"]
  }
}
```

- `completeness` is computed, not asserted: `exact` when the target has no R4/R5 in-edges and no unresolved sites naming it; `partial` otherwise.
- Agents can then implement the correct policy themselves ("if completeness != exact, also grep") — which converts UCI's weakness into an explicit, explainable handoff instead of a silent miss. **This is the difference between an agent being misled and an agent being informed.**

### 1.6 Record what you *couldn't* resolve as first-class facts

Add an `unresolved_call` record (site, name, receiver text, reason: `dynamic-receiver | getattr | fan-out-capped | not-found`). Three payoffs:

1. `completeness` above becomes computable.
2. It's the measurement substrate for §3 (you can now track "% of call sites resolved at R0–R3" per repo as *the* headline precision metric).
3. Future resolvers (type inference, LSP) have a worklist instead of re-scanning everything.

### 1.7 Add an optional LSP bridge as a high-precision edge source

Language servers already ship type-aware `callHierarchy`/`references`. An optional adapter (`uci enrich --lsp`) that runs Pyright/tsserver over the repo and **promotes or prunes R4/R5 edges** based on LSP answers would give UCI near-ground-truth call edges on machines that have the toolchain, at zero algorithmic cost to UCI itself. Fits the existing adapter philosophy perfectly (optional, lazy, never required for local-lite). Provenance: `extractor="lsp-pyright", resolution="inferred"`.

This is the cheapest path to actually *being* what the docs currently claim.

### 1.8 Word the promise you can keep

Replace, in `architecture.md` §6 and `retrieval-strategy.md` §8:

> ~~"answers are deterministic, explainable, and traceable"~~

with language of this shape:

> "Structural answers are **exact over resolved edges** (imports, same-scope calls, annotated types — the majority in typical code), and **explicitly labeled** where name-based resolution is the best available evidence. Every answer reports its own completeness."

That is a promise the R-ladder + completeness field actually keeps — and it is *more* credible to the target audience than the absolute claim, because every engineer evaluating UCI knows the absolute claim is impossible for Python.

---

## 2. P4 — Incremental indexing: re-index only what changed *without corrupting cross-file edges*

Content hashing tells you which files changed; it says nothing about the **edges from unchanged files into changed ones**. To keep P4 without graph rot:

1. **Two-phase pipeline** (extract → resolve):
   - *Extract* (per-file, hash-gated, parallelizable): parse changed files into `ParseResult`s; store raw symbols/imports/calls keyed by file.
   - *Resolve* (repo-scoped, incremental): rebuild name/import binding tables only for the **dirty set**, then re-resolve (a) all call sites *in* changed files and (b) all call sites *targeting symbols whose file changed*. The reverse edge index (`in_relationships`) already gives you (b) — use it to compute the invalidation frontier.
2. **Delete-before-add per file** for entities and *out*-edges (CodeRAG's pattern, already cited in `repo-comparison.md`), plus frontier re-resolution for in-edges. Never mutate in place.
3. **Index generations**: stamp every index pass with a generation ID + repo HEAD SHA in `state`. Readers (server, MCP) operate on the last *completed* generation; the in-memory hydrated graph swaps atomically on generation completion. This also resolves the unstated watch-vs-serve concurrency question.
4. **Escape hatch**: `uci index --full` and an automatic full-reindex trigger when the schema version or extractor version changes (stamp both in `state`).

---

## 3. Prove it: an evaluation harness (turns P1/P2/P7 from claims into measurements)

None of the promises survive scrutiny without numbers. Minimum viable harness, all CI-runnable without network:

1. **Golden call-graph fixtures**: 2–3 small repos (one already appearing under `tests/fixtures/sample_repo/`) with a hand-verified edge list. Metrics: **call-edge precision and recall, reported per resolution level**. This is the determinism scoreboard — "R0–R1 precision 1.0, R2 0.97, overall recall 0.83" is the honest version of the current prose.
2. **Golden retrieval queries** (~50): identifier lookups, NL questions, impact questions, each with expected entity IDs. Metrics: recall@5/10, MRR. Gate fusion-weight changes on this — today's weights (`symbol=1.4, …`) are folklore until measured.
3. **Impact-pack ground truth**: for ~10 symbols in fixtures, the full expected pack (callers/tests/config). This is the flagship query; it deserves its own regression suite.
4. **One large OSS repo benchmark** (run locally, not in CI): index Django or similar; report index time, DB size, query latency, % resolved at R0–R3. Publishes the scale envelope that `architecture.md` currently leaves unstated, and gives marketing honest numbers.
5. Add to the roadmap's **definition-of-done**: "no phase ships a retrieval/extraction change without eval deltas."

---

## 4. P5 — Explainability: extend the discipline to UCI's own outputs

The envelope design is excellent; three places currently violate its spirit:

1. **Risk score**: `0.72 "high"` with an undefined `f(...)` is flash, not explainability. Either publish the formula in `retrieval-strategy.md` (e.g., normalized weighted factors: caller count, test coverage presence, churn recency, fan-out — each reported alongside the score) or drop the scalar and return the factor list only. An agent can weigh factors; it cannot audit an opaque number.
2. **Truncation must be visible.** `bfs` caps at 200 results and depth caps exist ("bounded traversal" is promised as a safety feature) — but a *silently* truncated caller list is a false "complete blast radius". Add `"truncated": true, "limit": 200` to any capped result.
3. **Staleness must be visible.** Add `index: {generation, head_sha, indexed_at, commits_behind}` to the envelope (cheap: compare stored SHA to current HEAD). An agent reasoning over a 3-commit-old graph should know it — this is the temporal analogue of the `reason` field.

---

## 5. P6 — Secrets: redaction must happen at ingest, not at output

The promise is "no secrets in output; values redacted." Output-time redaction is too late if values enter the store:

1. **Never chunk or embed config-language files.** Index their *keys* as `CONFIG_KEY` entities (the `ConfigParser` design already does exactly this — good); exclude their bodies from `build_chunks`/`embed_chunks`. One `language == "config"` guard in the chunking entry point keeps `.env` values out of `chunks.text`, out of vectors, and out of any cloud embedding API.
2. **Entropy/pattern scrub for code files**: a light secret detector (long high-entropy literals, `AKIA…`, `-----BEGIN`) that masks matched literals in chunk text before storage. Best-effort is fine; state it as best-effort.
3. **State the trust boundary in the docs**: `.uci/uci.db` contains source text; treat it with the same sensitivity as the repo itself (don't sync it, add `.uci/` to backups/exclusions guidance). Honesty here costs nothing and pre-empts the criticism.

---

## 6. P7 — "Graceful degradation" and "beats embedding-only": tighten the two loose ends

1. **Rename the hash-embedding signal.** In local-lite the "semantic" row of the degradation matrix is token-hash overlap — lexical recall, not semantics. Label it `lexical-hash` in docs, config, and the `signals[]` array. Users who want real semantic recall then correctly reach for the `embeddings` extra or Ollama — which is precisely the upgrade path the profiles ladder wants to sell.
2. **Guard against mixed-model vector stores.** Silent Ollama→Local fallback (and model swaps between runs) can leave incompatible vectors in one table, quietly corrupting the semantic signal — a graceful-degradation mechanism producing an *un*-graceful result. Record `(provider, model, dim)` in index state; on mismatch, either re-embed or disable the semantic signal with a visible warning in `stats.signals_used`.

---

## 7. P8 — One graph, two audiences: decide the semantics strategy

The dashboard promises (overview, architecture summary, onboarding) exceed what structural facts alone can express. Two defensible paths — pick one explicitly:

- **(a) Structural-honest dashboard**: scope local-lite views to what the graph proves — modules, dependency layers from import topology, symbol search, graph explorer, impact, churn heat. Rename "onboarding guide" to "dependency-ordered reading path" (topological sort is structural — Understand-Anything's tour trick works without an LLM).
- **(b) Optional LLM-enrichment adapter** *(recommended — it fits the existing architecture perfectly)*: an `uci enrich` pass that writes `summary`, `layer`, `domain`, `capability` attributes onto existing nodes with `extractor="llm:<model>", confidence<1.0`. The provenance/confidence machinery was *built* for this; enrichment becomes just another extractor, the dashboard reads the same graph, and local-lite stays pure. This also finally gives the business/domain entity types a producer.

---

## 8. Documentation & positioning changes (cheap, do immediately)

1. **Re-mark the roadmap**: ✅ only for what exists and is tested; 🚧 for scaffolded; ⏳ for planned. Split "designed" from "delivered". Nothing erodes trust in a promise-driven project faster than unearned checkmarks.
2. **Adopt the reworded determinism claim** (§1.8) everywhere the absolute claim appears.
3. **Add the missing competitive section** (LSP/SCIP/Glean/CodeQL) to `repo-comparison.md`, with the honest differentiator: *persistent, multi-language, beyond-code graph, cheap enough for every repo, one explainable contract for agents and humans*. This reframes LSP from unacknowledged rival to acknowledged complement (§1.7 even uses it as a data source).
4. **Tier the schema** in `canonical-schema.md`: Populated / Planned / Aspirational. Register MCP tools dynamically based on which edge types exist in the index, and say so — a documented tool that always returns `[]` teaches agents to distrust the server.
5. **State scale envelopes** per profile once §3.4's benchmark produces numbers.

---

## 9. Priority order (if you can only do five things)

| # | Change | Promise saved | Effort | Leverage |
| --- | --- | --- | --- | --- |
| 1 | Resolution ladder + `resolution`/`confidence` policy (§1.2–1.4) | P1/P2 | Medium | **Highest — redefines the core claim into a keepable one** |
| 2 | Stratified impact pack + `completeness` field (§1.5–1.6) | P1/P5 | Small (given #1) | Turns silent misses into explicit handoffs |
| 3 | Golden fixtures + precision/recall in CI (§3) | P1/P2/P7 | Small | Makes every other claim measurable |
| 4 | Two-phase extract/resolve with frontier invalidation (§2) | P4 | Medium | Prevents the graph from rotting — the failure users hit in week two |
| 5 | Config-content exclusion from chunking (§5.1) | P6 | Tiny | Closes the one outright broken promise |

Then: staleness + truncation in the envelope (§4), rename the hash signal (§6.1), roadmap re-marking (§8.1), LSP bridge (§1.7), LLM-enrichment adapter (§7b).

---

## 10. Phase 2 (next iteration) — Gap Registry: report what the index *doesn't* know

> **Status:** proposed as the iteration immediately following the resolution-ladder work above. Full design spec: `docs/next-iteration-gap-registry.md`. Summarized here so the phase is visible in this document's plan; the spec is the source of truth.

**Principle:** never discard a resolution failure — at the moment of failure the extractor almost always knows the *name* of what's missing (copybook member, called program, JCL PROC, expected import path) and often its expected origin (SYSLIB, JCLLIB, repo path).

**Scope of the phase:**
1. **Placeholder (stub) entities** for unresolved edge targets (`attributes.missing=true`, reserved `__missing__` id segment) — edges stay in the graph, labeled, and **self-heal** when the artifact is later indexed.
2. **`gaps` store + `report_gap()` extractor convention** — one helper every resolver calls instead of silently dropping; generalizes §1.6's `unresolved_call` worklist to "unresolved anything".
3. **Surfaces:** `uci gaps` CLI (acquisition checklist ranked by fan-in — *"obtaining copybook `PAYROLL01` resolves 240 dangling references"*), `list_index_gaps` MCP tool, and `completeness.gaps` citations in impact packs (builds on §1.5).
4. **Missing-vs-external classifier** so stdlib/vendor/system modules (`DFH*`, `SYSIBM.*`, `numpy`, …) become external stubs, not acquisition noise.

**Why this phase must come early:** it is a *convention* extractors follow, not a feature bolted on — nearly free to establish while extractors are few, painful to retrofit across ten of them later. For legacy/mainframe estates the ranked "source you still need to obtain" report is a deliverable in its own right; for agents it is the difference between being misled by the graph and being told exactly where the graph ends.

**Acceptance criteria (from the spec):** removing a fixture file yields a gap naming it, its expected path, and all referencing sites; restoring it heals every edge and auto-closes the gap; impact analysis cites stub callers in `completeness.gaps`; stdlib/vendor imports produce zero gap records.

---

## 11. Phase 3 — Post-implementation review: findings from verifying `recommendations-status.md`

> **Basis:** every ✅ claim in `recommendations-status.md` was verified against the code on 2026-07-01 (all source read; full test suite executed). **Overall verdict: the implementation is real and largely faithful** — the resolution ladder, stratified impact packs, completeness computation, staleness/truncation reporting, config-file chunk exclusion + secret scrub, embedding-model guard, lexical-hash renaming, and the doc rewrites all exist and work as described. The Phase 2 gap registry is even implemented ahead of its spec (stubs, `gaps` table, `uci gaps`, `list_index_gaps`, `completeness.gaps` citation, mainframe-aware external prefixes `DFH/DSN/CEE/DFS/SYSIBM`). The items below are what verification found **incorrect, partial, or overstated** — this is the Phase 3 worklist.

### 11.1 Correctness bugs (fix first)

1. **Ambiguous import candidates leak into the *resolved* stratum** — `ingest/graph_builder.py:436-437`. When multiple imported candidates match a callee, the code picks the first and labels it `"import-traced"` with confidence 0.6. `import-traced` ∈ `RESOLVED_LEVELS`, so this **ambiguous guess** (a) lands in `callers.resolved` in impact packs, (b) survives the speculative-edge filter in `hybrid._graph_signal`, and (c) drives multi-hop traversal in `engine._call_graph` — all three protections of §1.4/§1.5 bypassed by one mislabel. **Fix:** label it `candidate` (or a new `import-ambiguous` level excluded from `RESOLVED_LEVELS`), confidence `min(0.4, 1/N)`, keep `fan_out`.
2. **Speculative edges still drive multi-hop through node expansion** — `engine.py:_call_graph`. R4/R5 edges are correctly reported only at depth 1, **but nodes reached via a speculative edge are appended to the frontier**, so a path `A -(candidate)-> B -(resolved)-> C` reports C at depth 2 — a blast radius built on a speculative hop. **Fix:** don't add a node to the frontier when the edge that reached it is unresolved.
3. **Failing test / false status claim** — `tests/test_api.py::test_api_mcp_tools` asserts 10 tools; there are 11 (`list_index_gaps` was added without updating the test). Actual suite result: **105 passed, 1 failed** — the status doc's "106 tests pass" is false. Fix the assertion (count from `TOOL_SPECS`, not a literal) and correct the status doc.

### 11.2 Partial implementations marked ✅ (finish or re-mark)

4. **The resolution ladder covers `CALLS` only.** §1.2 mandates `resolution` on `CALLS`/`REFERENCES`/`EXTENDS`/`IMPLEMENTS`. `_resolve_inheritance` resolves base classes by **global first-match on bare name** (`targets[0]`, no module/import narrowing) and emits `EXTENDS`/`IMPLEMENTS`/`REFERENCES` edges with **no resolution label and default confidence 1.0** — the exact overconfidence the ladder exists to prevent, now concentrated on inheritance edges (which also feed `_ancestor_map`, so a wrong EXTENDS edge corrupts R3 call resolution downstream). Status doc marks 1.2 ✅; it is partial.
5. **`unresolved_call` recording captures only `fan-out-capped`.** §1.6 specified reasons `dynamic-receiver | getattr | fan-out-capped | not-found`. Zero-candidate callees return silently (`graph_builder.py:417`) — no record, no gap — and dynamic receivers are never recorded. Consequence: `completeness` reports **"exact" for symbols whose callers are hidden behind dynamic patterns**, which is the one lie the completeness field was built to prevent. Fix: record `not-found` (with a builtins/stdlib filter to control noise) and `dynamic-receiver`; wire call-target gaps per the Phase 2 spec (`call-target-not-indexed`).
6. **R2 "inferred" trusts a global bare-name class lookup.** `_class_named` returns the first class matching the receiver's name repo-wide; two same-named classes in different modules yield a possibly-wrong method binding at confidence 0.9. Narrow by the caller module's imports/bindings first; degrade to `candidate` when multiple classes match. (Same first-match pattern to review: `_resolve_inheritance` above, and `by_qname[...][0]` caller selection.)
7. **`get_callers`/`get_callees` completeness ignores unresolved sites.** `engine._call_graph` computes completeness from candidate edges only; `impact.analyze` additionally consults `unresolved_calls` naming the target. The two surfaces can disagree ("exact" vs "partial") for the same symbol. Reuse the impact analyzer's `_unresolved_for` in `_call_graph`.

### 11.3 Documentation drift created by the implementation

8. **`mcp-tools.md` is stale**: the catalog lists 10 tools ("The first eight are fully wired") — `list_index_gaps` is missing, and the dynamic-availability behavior (`available` annotation from `engine.capabilities()`) that the status doc claims under 8.4 is implemented in code but undocumented. This same staleness produced finding #3.
9. **"Weights are configurable" (`retrieval-strategy.md` §2) is still only half-true**: `Config` has weight fields but `from_env` reads no `UCI_WEIGHT_*` keys — configurable in code, not by users. Wire the env keys (and add them to `.env.example`) or reword the claim.
10. **Incremental promise wording**: the indexer's actual model is *graph = always full rebuild; embeddings = hash-incremental* — a defensible design honestly described in the indexer docstring and status doc (🚧), but `architecture.md` §1.7 still says "re-indexes only what changed" unqualified. Align the principle text; the two-phase frontier design (§2) remains the roadmap for scale.

### 11.4 Hygiene

11. Delete `analysis/onboarding_init_placeholder.py` — a stray duplicate of `analysis/__init__.py` left over from generation; dead code that will confuse contributors.
12. `engine.find_symbol(exact=True)` falls back to fuzzy matches when no exact match exists (`... or matches`) — surprising contract for a parameter named `exact`; return empty (or flag the fallback in the response) instead.
13. `_keyword_signal` scans every entity and chunk in Python per query — acceptable at fixture scale, but it is the hot path for every search; the promised SQLite FTS5 keyword signal (retrieval-strategy §1) remains unimplemented and should be scheduled before any scale benchmark.

### 11.5 Phase 3 exit criteria

- Findings 1–2 fixed and covered by tests that assert **no unresolved-labeled edge appears in a `resolved` stratum and no multi-hop path crosses a speculative edge** (fixture: two modules importing same-named callables).
- Finding 5 fixed and covered by a test where a dynamically-dispatched caller exists: `completeness.level` must not be `exact`.
- Inheritance edges carry `resolution` labels (finding 4); ambiguous base names degrade below `RESOLVED_LEVELS`.
- Full suite green (including the corrected tool-count test); `recommendations-status.md` re-verified line by line against this section and updated — the status document must hold to the same honesty standard §8 demands of the roadmap.

---

## 12. Second audit — §10/§11 status verified; Phase 4 residuals

> **Audit of `recommendations-status.md` §10–§11 (2026-07-01):** every claim verified against the code and test run. **All Phase 3 findings are genuinely fixed**: ambiguous import candidates now degrade to `candidate` (`graph_builder.py:512`), speculative edges no longer seed the traversal frontier (`engine.py:153`), inheritance edges carry ladder labels via `_resolve_type_ref` and `_ancestor_map` consumes only resolved edges, `_unresolved_reason` records `not-found`/`dynamic-receiver` with a builtins/binds filter, R2 uses `_narrowed_class`, caller completeness consults unresolved sites, `UCI_WEIGHT_*`/`UCI_RRF_K` are env-wired and in `.env.example`, `mcp-tools.md`/`architecture.md` are updated, the stray file is deleted, and the suite is **121 passed** (verified by execution, not by trusting the doc). The gap registry (§10) is likewise implemented as claimed, including the dashboard page and dashed/⟂ stub rendering.

The findings below are **new** — precision gaps visible only in the *fixed* code. None is severe; together they form the Phase 4 worklist.

### 12.1 Aliased type references dead-end before the binding table — `graph_builder._resolve_type_ref`

The candidate lookup (`by_name.get(name.lower())`) runs **before** the binds check, and returns early when empty (`graph_builder.py:304-306`). For `from x import Thing as T; class C(T)`, the bare ref name `T` matches no class by name, so the method returns `None` **without ever consulting `binds`**, where `T → x.Thing` sits ready to resolve it. Aliased base classes — a common pattern — silently produce no `EXTENDS` edge. **Fix:** check `binds` first (it is the higher rung anyway), then fall back to name candidates.

### 12.2 Unresolved type references vanish silently — the inheritance analog of §1.6 is missing

When `_resolve_type_ref` finds no target, `_resolve_inheritance` just `continue`s (`graph_builder.py:334-335`): no `unresolved_ref` record, no gap. A base class from an unindexed *internal* module disappears — yet a missing base is exactly the polymorphic-risk case impact analysis cares about (`overrides` in the impact pack design), and the gap registry exists precisely for "referenced but not indexed." **Fix:** apply the §1.6/§10 convention — record an `unresolved_ref` fact and, when the name traces to an internal module (via binds/import map), `report_gap("class", …)`; external bases (e.g. `pydantic.BaseModel`) go the external-stub route.

### 12.3 `get_callees` completeness is asymmetric — hidden *callees* aren't counted

`_call_graph` consults `_unresolved_naming` only for `direction == "in"` (`engine.py:156`). For `get_callees`, the target's **own unresolved call sites** (recorded with `caller == target.qualified_name`) are the hidden callees — a function full of dynamic dispatch reports `completeness: exact` for its callee list. Same one-line pattern as the caller fix: filter `unresolved_calls` by `caller` instead of `name` for the "out" direction. (The impact pack's `callees` stratum has the same blind spot.)

### 12.4 Stub entities enter retrieval results unlabeled

Stubs are indexed into `by_name`/`by_qname` and persisted to the graph, so they can surface through search, graph expansion (IMPORTS edges are in `DEPENDENCY_LIKE`), and `find_symbol` — but `RetrievalHit` carries no `missing`/`external` flag, so an agent can receive a placeholder with `path: ""` as an ordinary result and try to read source that doesn't exist. The dashboard got this right (dashed ⟂ rendering); the agent surfaces didn't. **Fix:** propagate `missing`/`external` from entity attributes into hit dicts (and into `_entity_hit`), and let `search` optionally exclude stubs by default.

### 12.5 Tracked, not new (no action needed now)

- Call-target gaps (`call-target-not-indexed`) remain ⏳ in §10 — correctly scoped to land with the extractor convention.
- FTS5 keyword index remains a documented pre-benchmark task (`retrieval-strategy.md` §1 note).
- `_unresolved_naming` matches hidden callers by bare name globally — over-reports incompleteness for common names; acceptable until golden fixtures (§3) can measure the false-partial rate.

### 12.6 Phase 4 exit criteria

- A fixture with `from x import Thing as T; class C(T)` yields an `EXTENDS` edge labeled `import-traced` (12.1).
- A fixture inheriting from a class in a deliberately-unindexed internal module yields a gap record and non-`exact` completeness where the base is consulted (12.2).
- `get_callees` on a function containing dynamic call sites reports `completeness: partial` with `unresolved_sites > 0` (12.3).
- `search`/`find_symbol` responses mark stub hits (`missing: true`) and a test asserts no unlabeled stub reaches an agent surface (12.4).
- Suite green; `recommendations-status.md` §12 row added with the same per-item verification discipline as §11.

---

## 13. Third audit — §12 status verified; Phase 5 residuals

> **Audit of `recommendations-status.md` §12 (2026-07-01):** verified by code read and test execution (**125 passed**, matching the claim). Fixes 12.1, 12.2, and 12.4 are correct and complete: `_resolve_type_ref` consults the binding table first; `_maybe_ref_gap` records class gaps for internal bases and external stubs for vendor bases; `RetrievalHit`/`_entity_hit`/graph nodes carry `missing`/`external`, and both `search` and `find_symbol` exclude stubs. The findings below are what this audit turned up — two substantive, two minor.

### 13.1 Fix 12.3 is partial but marked ✅ — the impact pack still has the hidden-callee blind spot

`engine._call_graph` now consults `_unresolved_from` for the "out" direction — correct. But §12.3 explicitly named the second surface: *"the impact pack's `callees` stratum has the same blind spot"* — and `ImpactAnalyzer.analyze` was not touched. It still computes `completeness` from caller-side signals only (`_unresolved_for`), and its `callees` object has no `unresolved` block at all. So `impact_analysis` on a function full of dynamic dispatch reports its dependency picture without any caveat, while `get_callees` on the same symbol correctly says `partial`. Two surfaces answering the same question differently is precisely what the "one engine, thin adapters" architecture exists to prevent. **Fix:** add `callees.unresolved` (from unresolved sites where `caller == target`) to the impact pack and fold it into `_completeness`. And per the §11.5 exit-criteria discipline: a ✅ requires *all* surfaces named in the finding, not the first one.

### 13.2 A binds miss falls through to global name-match — contradicting known evidence

In both `_resolve_callee` (`graph_builder.py:501-517`) and `_resolve_type_ref` (non-aliased path), when the binding table *names the origin* but the target isn't indexed — `from missing_mod import Thing`, `missing_mod` absent — the resolver falls through to **global bare-name candidates**. If some unrelated module defines a class/function with the same name, the edge binds to it (`name-match` 0.6, or `candidate`) even though the import statement says the symbol comes from somewhere else entirely. The binding table is the *strongest* evidence available; when it points at a missing artifact, the correct outcome is the §10 convention (gap + stub edge, as `_maybe_ref_gap` already does for the aliased case), never a fallback to weaker evidence that contradicts it. **Fix:** in both resolvers, a binds hit whose target is unindexed short-circuits to `report_gap` — no name-candidate fallback. This also closes the asymmetry where aliased missing bases produce gaps but non-aliased ones produce wrong edges.

### 13.3 Minor: edges to external stubs are labeled `resolution="missing"`

`_maybe_ref_gap` uses `{"resolution": "missing"}` unconditionally (`graph_builder.py:362`), including for external stubs (`missing=False, external=True` on the entity). An agent filtering edges by resolution can't distinguish "artifact we should obtain" from "vendor dependency, expected absent." Label external-stub edges `resolution="external"`.

### 13.4 Accepted (record the decision, no action)

- Unresolved type references **not** traceable through the binding table are still silently skipped (`_maybe_ref_gap`'s early return) — a documented noise-control decision, reasonable until wildcard-import support exists. Recorded here so the skip is a choice, not an oversight.
- Call-target gaps, FTS5, and global-name over-report remain tracked as before (§12.5).

### 13.5 Phase 5 exit criteria

- `impact_analysis` on a fixture function with dynamic call sites reports `callees.unresolved.count > 0` and non-`exact` completeness, matching `get_callees` on the same symbol (13.1).
- A fixture with `from missing_mod import Thing; class C(Thing)` where another module also defines `Thing`: **no** EXTENDS edge to the unrelated `Thing`; a `class`/`module` gap names `missing_mod.Thing` (13.2). Same shape for a called function.
- External-stub edges carry `resolution="external"`; a test asserts no `missing`-labeled edge points at an `external=True` entity (13.3).
- Suite green; status doc §13 row verified per-surface before any ✅.

### 13.6 Fourth audit (2026-07-01): §13 verified clean — audit loop closed

`recommendations-status.md` §13 audited by code read and test execution (**129 passed**). All three findings fixed correctly **on all named surfaces** this time: the impact pack gained `callees.unresolved` and folds it into completeness (parity with `get_callees`); `_resolve_via_binds` short-circuits bound-but-unindexed targets to gaps before the global ladder, and `_resolve_type_ref` does the same for type refs; external-stub edges carry `resolution="external"`. Residuals are cosmetic only (dead binds-branches in `_unresolved_reason` now unreachable behind `_resolve_via_binds`; the undocumented `fan_out: -1` sentinel on stub edges; JS lacks the instantiation-reference path that gaps missing constructors in Python) — recorded here, not worth a phase. **No Phase 6 worklist. The audit-fix loop has hit diminishing returns; the next defect-finding instrument should be the evaluation harness (§3), not another read-through.**

---

## 14. Closing note

UCI doesn't need to abandon its determinism promise — it needs to **relocate** it. The graph layer already is deterministic; the extraction layer never will be, for dynamic languages, and pretending otherwise sets the project up to be judged by its worst edge. The resolution ladder, completeness reporting, and an eval scoreboard convert "trust us, it's deterministic" into "here is exactly which answers are exact, which are inferred, and how we measure it" — a strictly stronger promise, and one the current architecture (provenance, confidence, adapters, explainable envelopes) is unusually well-prepared to keep.
