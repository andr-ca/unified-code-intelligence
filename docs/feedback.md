# Concept Feedback — Unified Code Intelligence (UCI)

**Reviewed:** 2026-07-01
**Scope:** The idea and design as expressed in `docs/` — `architecture.md`, `canonical-schema.md`, `retrieval-strategy.md`, `mcp-tools.md`, `repo-comparison.md`, `roadmap.md`. This is feedback on the **concept**, not an audit of the implementation.

---

## 1. Executive summary

UCI's core thesis — *a canonical knowledge graph as the source of truth for code intelligence, with embeddings demoted to one retrieval signal among many, serving agents and humans from the same graph* — is **correct, well-argued, and well-timed**. It fixes the real weakness of embedding-centric RAG (inability to answer exact structural questions), and it fixes the real weakness of each reference project it synthesizes: CodeRAG's underused graph, code-graph-rag's heavy infrastructure, Understand-Anything's dead-snapshot graph that agents can't query.

The design's strongest ideas are the **explainable retrieval contract** (every hit carries provenance, a reason, and suggested next queries), **graceful degradation as a first-class design axis**, and **local-first zero-dependency operation** as the default profile.

The concept's main weaknesses are: (1) it **overpromises determinism** — a name-resolution call graph in Python/JS is heuristic, and the docs' "deterministic, explainable, complete" framing will not survive contact with dynamic dispatch; (2) the **competitive analysis stops at three hobby-scale RAG projects** and never engages the real prior art for structural code intelligence (LSP, SCIP/Sourcegraph, Glean, CodeQL, Kythe), which weakens both the positioning and the design; (3) there is **no evaluation story** — a retrieval system with six fused signals and hand-picked weights, but no benchmark, cannot claim it beats anything; and (4) the **schema's breadth (40+ entity types spanning code to COBOL to business capabilities) is ahead of any plausible extractor roadmap**, a classic boil-the-ocean pattern.

**Verdict: the thesis deserves to be built.** The recommendations below are mostly about narrowing claims, confronting the hard problems the docs currently step around, and positioning against the systems that already do structural code intelligence at scale.

---

## 2. The core thesis — what it gets right

### 2.1 "Graph is the source of truth; embeddings are one signal"
This is the right corrective, and `architecture.md` §6 argues it precisely: embedding-only systems answer *"what looks similar?"* but not *"who calls this?"*, *"what breaks?"*, *"which tests cover it?"*. Three design consequences follow correctly:

- **Retrieval degrades gracefully** (`retrieval-strategy.md` §7's degradation matrix is an excellent artifact — most systems never write down what happens when a dependency is absent).
- **Structural queries are deterministic *lookups*, not similarity guesses** — the right mental model for agent tooling, where a wrong-but-confident answer is worse than "not indexed."
- **Embeddings become an optional recall enhancer**, which is what they actually are for code.

### 2.2 "One graph, two audiences"
Serving agents (MCP/JSON) and humans (dashboard) from the same canonical graph is the genuinely novel synthesis here. Understand-Anything proved the human-facing "graph that teaches" concept but left it as a dead JSON snapshot; code-graph-rag proved agent-facing graph tools but has no human surface. Unifying them means the dashboard's impact view and the agent's `impact_analysis` tool can never disagree — a quietly valuable property for teams adopting agent workflows.

**One unaddressed tension (see §6.3):** the human views that made Understand-Anything compelling (summaries, domain grouping, personas, tours) require *semantic* enrichment that Understand-Anything got from an LLM. UCI's local-lite profile has no LLM. The docs promise the dashboard without confronting what it can show from purely structural facts.

### 2.3 Explainability as a contract, not a feature
The common result envelope (`mcp-tools.md` §1) — `entity_id`, path, line range, `signals[]`, `reason`, `confidence`, `relationship_path`, `next_queries[]` — is the best single design decision in the docs:

- `reason` + `relationship_path` make results **auditable** by the agent and the human reviewing the agent.
- `next_queries` turns each tool response into a **guided exploration step** — this matches how agents actually chain tool calls and reduces flailing.
- Deterministic IDs that **round-trip** back into any tool close the loop.

This envelope is worth standardizing and publishing on its own; it's a better agent-tool contract than most shipping MCP servers use.

### 2.4 Local-first, stdlib-only default
The `local-lite` profile (SQLite + in-memory graph + hash embeddings, zero pip dependencies) is a strong adoption wedge. CodeRAG demonstrated that "pip install, no Docker, no API key" is the DX bar for this category; UCI adopting it while adding the graph is the right call. The three-profile ladder (local-lite → local-pro → cloud) behind stable interfaces gives a credible growth path without contaminating the default.

### 2.5 Honest lineage
`repo-comparison.md` is a model prior-art document: per-project reusable concepts *and* limitations, explicit licensing conclusions, clean-room framing, and a one-line "what UCI takes and what it improves" per source. Most projects never write this down. The specific steals are also the *right* ones (RRF fusion and content-hash incrementality from CodeRAG; graph-as-truth and MCP-as-thin-adapter from code-graph-rag; multi-view teaching UX from Understand-Anything).

---

## 3. The biggest conceptual gap: the competitive frame is too small

The comparison covers three open-source RAG-era projects — but UCI's actual claims ("who calls this?", "what breaks if I change it?", cross-referenced symbols with provenance) describe **code indexing and static analysis**, a field with mature, battle-tested prior art that the docs never mention:

| System | What it already does | Why it matters to UCI |
| --- | --- | --- |
| **LSP servers** | `callHierarchy`, `references`, `definition`, `implementations` — per-language, *type-aware*, already on every dev machine | An agent with LSP access already gets more accurate callers/callees than a name-resolution graph. UCI must articulate what it adds: **persistence, cross-language uniformity, non-code entities (config/tests/churn), and query-ability without a running language server.** That's a real answer — but the docs never make it. |
| **SCIP / Sourcegraph** | Cross-repo precise code intelligence via typed indexers | UCI's `entity_id` scheme is solving the same problem SCIP symbols solve; adopting or mapping to SCIP would buy interop with existing indexers for free. |
| **Glean (Meta), Kythe (Google)** | Canonical fact schemas over code at monorepo scale | Glean's "predicates + facts + derived facts" is exactly UCI's schema concept, hardened by a decade of production. Worth studying for schema evolution and incremental invalidation patterns. |
| **CodeQL** | Code as a queryable relational database | The "impact pack" is a fixed-shape specialization of what CodeQL queries express generally. |

**Why this matters beyond positioning:** these systems already hit — and solved — the hard problems UCI's docs currently gloss (call resolution precision, cross-file incremental invalidation, schema versioning, scale). Not engaging them means re-deriving their lessons the slow way.

**Recommendation:** add a section to `repo-comparison.md` positioning UCI against LSP and SCIP at minimum. The honest differentiator is strong: *"a persistent, multi-language, beyond-code knowledge graph that is cheap enough to run on every repo and exposes one explainable contract to agents and humans."* No LSP server or CodeQL gives you config-keys→component edges, churn signals, test coverage edges, and a dashboard from one index. Say that.

---

## 4. The canonical schema (`canonical-schema.md`)

### Strengths
- **Generic storage with typed kind + JSON attributes** means adding entity types requires no migration — the right physical design for an evolving taxonomy.
- **Soft validation** (warn, never fail, on unexpected src/dst kinds) is the correct robustness posture for a graph fed by heterogeneous extractors and, later, LLMs.
- **Alias normalization** as a schema-layer concern (learned from Understand-Anything) is the right place for it.
- **Stable readable IDs with hashed repo roots** balance debuggability against path leakage — thoughtful.
- The worked example (§6, `pricing/calculator.py` → nodes and edges) is exactly how a schema doc should teach.

### Concerns

**4.1 Breadth is ahead of demand.** ~40 entity types and 26 relationship types, of which the MVP populates perhaps a third. Six COBOL/mainframe types and four business-domain types exist with no extractor, no consumer, and no committed timeline. The counterargument — "schema-ready costs nothing thanks to generic storage" — is only half true: every type in the taxonomy is API surface that extractor authors, tool consumers, and dashboard designers must understand, and unpopulated types erode trust ("why does `find_data_lineage` always return nothing?"). The MCP doc already shows this strain: two of ten cataloged tools "return whatever the current extractors have populated" — i.e., nothing.

*Recommendation:* keep the taxonomy but **tier it explicitly** — Tier 1 (populated now), Tier 2 (next two quarters), Tier 3 (aspirational/legacy). Don't catalog MCP tools whose backing edges don't exist; add tools when their edges land.

**4.2 Directionality and aliases need more rigor.** The doc maps `tested_by → TESTS` as a name alias — but `tested_by` is the *reverse direction*. A schema layer that normalizes names must also normalize direction (return `(type, flipped)`), or reversed-perspective aliases must be excluded. Similarly, "`uses, invokes(call) → CALLS (context-aware)`" promises context-sensitive normalization that is genuinely hard (`uses` and `invokes` are *also* canonical types in the taxonomy — the same string means different edges in different domains). This ambiguity is self-inflicted: **avoid alias strings that collide with canonical type names.**

**4.3 No schema versioning story.** The doc covers adding types but not evolving them: what happens to persisted graphs when a type's meaning shifts, an edge's expected endpoints change, or an extractor's qname convention changes (invalidating every derived ID)? Incremental indexing (a headline principle) makes this acute — old and new extractor output will coexist in one graph. A `schema_version` on the index plus a documented "re-index on major bump" policy would be enough for now; the doc should say *something*.

**4.4 `SYMBOL` as silent fallback.** Unknown kinds normalizing to a generic `SYMBOL` keeps ingestion alive (good) but silently discards information. The concept should mandate preserving the original string in attributes — "degrade gracefully" should mean *recoverably*.

---

## 5. Retrieval strategy (`retrieval-strategy.md`)

### Strengths
- **RRF over six signals** is the right fusion choice for incomparable score scales, and adaptive identifier-vs-prose routing is a proven pattern (correctly attributed to CodeRAG).
- **The impact pack** (§4) is the flagship, and its shape is excellent: callers, callees, tests, overrides, config, data, siblings, churn, risk — this is *the* question ("what breaks if I change this?") that neither grep nor embeddings answer, structured for agent consumption.
- **`retrieve_edit_context`** (§5) — "everything an agent needs to safely edit a symbol" with a derived checklist — is the most agent-native concept in the whole design. This is the tool that would change how agents edit code.
- The **degradation matrix** (§7) again — genuinely good design hygiene.

### Concerns

**5.1 The determinism claim oversells what name resolution can deliver.** §8 claims graph answers are "deterministic," "complete," and find "relevant-but-dissimilar code." But for Python and JS — the two MVP languages — a call graph built from `ast`/regex name matching without type inference is **heuristic**:

- `obj.calculate(cart)` — which `calculate`? Every class with that method name is a candidate. Duck typing, dependency injection, callbacks, decorators, `getattr`, framework dispatch (Flask routes, signals, DI containers) all break name-based edges.
- The result is a call graph with both **false edges** (over-linking common names — every `.get()`, `.run()`, `.process()`) and **missing edges** (dynamic dispatch) — and impact analysis inherits both. A "who calls this?" answer that silently misses callers is *more* dangerous than grep, because it arrives wearing a `confidence` field and a deterministic ID.

The schema already has the right primitive (per-edge `confidence`, `resolution` attribute) — but the *strategy* never discusses resolution policy: what happens to ambiguous calls (one edge per candidate? none? capped fan-out for common names?), how confidence is assigned, and how `impact_analysis` presents uncertainty ("3 confirmed callers, 14 possible via name match"). This is the single most important unwritten section in the docs. code-graph-rag's type-inference processor and every LSP implementation exist precisely because of this problem.

*Recommendation:* add a "call resolution & uncertainty" section. Distinguish **resolved** edges (import-traced, self/cls-typed, same-module) from **candidate** edges (name-only). Make impact packs report the two classes separately. Consider optional LSP piggybacking as a high-precision edge source — it's free accuracy on machines that have language servers anyway.

**5.2 No evaluation harness anywhere in the concept.** Six signals, hand-tuned weights (`symbol=1.4, keyword=1.0, …`), an RRF `k=60`, adaptive routing, a claimed "+5–15% MRR" from reranking (a number inherited from CodeRAG's context, not measured on UCI) — and no benchmark, no golden-query set, no retrieval metrics in any doc including the roadmap's definition-of-done. Without an eval set, weight tuning is folklore and every retrieval change is a blind bet. This is the cheapest high-leverage addition to the concept: ~50 golden queries over 2–3 fixture repos (mix of identifier lookups, NL questions, impact questions) with recall@k / MRR tracked in CI.

**5.3 The risk score needs a definition or a demotion.** `risk: f(callers, tests?, churn, fan-out) → 0.72 "high"` — agents and humans *will* act on this number. Either define the formula in the docs (so it's auditable, like everything else in UCI) and validate it against something (e.g., historical bug-fix commits touching high-risk symbols), or present factors without a scalar score. An unexplainable number is off-brand in a system whose motto is "explainability over flash."

**5.4 "Semantic" is a misleading label in local-lite.** The default embedding is hash-based token overlap — lexical, not semantic. The degradation matrix should label the row honestly ("hash/lexical recall") so users don't think local-lite gives them meaning-based search. The concept of a dependency-free fallback is good; the naming oversells it.

---

## 6. Architecture & deployment (`architecture.md`)

### Strengths
- The module map (brief-name → package → responsibility) and the two-phase data-flow diagram are exemplary — a contributor can locate themselves in the system in one read.
- Single-file SQLite persistence with in-memory hydration is the right local-first storage shape.
- "No core module imports a vendor SDK; adapters import lazily and fail with a clear message" — the correct plugin discipline, stated crisply.
- Contract tests as the mechanism keeping backends behaviorally identical is the right testing concept for a pluggable system (it must actually exist, though).

### Concerns

**6.1 Incremental indexing meets cross-file edges — the hard problem is unaddressed.** Content-hash change detection (principle #7) tells you *which files* changed. But the graph's value is **cross-file edges**: file A's `CALLS`/`IMPORTS`/`EXTENDS` edges point at symbols in file B. When B changes (a rename, a signature change), edges *from unchanged A* are now stale — re-indexing only changed files corrupts exactly the relationships the system exists to answer. Every serious code-indexing system (Glean, Kythe, SCIP) has an explicit answer (two-phase extract/resolve, reverse-dependency invalidation). UCI's docs never mention it. This is the second most important unwritten section.

*Recommendation:* design for a **two-phase pipeline**: per-file extraction (parallelizable, hash-gated) producing unresolved references, then a repo-wide (or dirty-subgraph) **resolution pass** that rebuilds name→symbol bindings. The reverse edge index makes "which files reference symbols in B" queryable — use it to compute the invalidation frontier.

**6.2 No scale envelope is stated.** In-memory graph hydration, brute-force vector scans, and BFS traversal are all fine — up to some repo size the docs never name. The audience that most needs impact analysis (large, old, tangled codebases) is precisely where these choices strain. The profile ladder is the right escape hatch, but the concept should state its envelope ("local-lite targets repos up to ~5k files / ~500k LOC; beyond that, local-pro") so users self-select correctly and the team knows what to benchmark.

**6.3 The dashboard promise exceeds structural facts.** Phase 3 promises overview, architecture summary, onboarding guide — Understand-Anything's versions of these were LLM-generated (summaries, domain grouping, layer classification). From purely structural facts, "architecture/layer inference" is directory-and-import heuristics: usable, but far from the "graph that teaches." The concept should either (a) scope the local-lite dashboard honestly to structural views (modules, search, graph explorer, impact), or (b) make LLM enrichment an explicit optional adapter with defined graph writes (`summary`, `layer`, `domain` attributes with `extractor="llm", confidence<1`), which the schema's provenance design supports beautifully. Option (b) is the stronger product; the docs just need to choose.

**6.4 Watch mode + reads = an unstated concurrency model.** `uci watch` re-indexes while `serve`/`mcp` answer queries from the same SQLite file and hydrated in-memory graph. Who sees half-updated graphs? SQLite WAL gives snapshot reads; the in-memory replica has no stated refresh/invalidations story. One paragraph ("index generations; readers swap on completed generation") would close it.

---

## 7. MCP tool design (`mcp-tools.md`)

The strongest document. Specific praise:

- **Ten tools is the right count** — small enough for an agent to hold, each with an obvious purpose. Compare code-graph-rag's kitchen-sink (shell execution, file writes) — UCI's read-only, context-not-edits posture (§4) is the safer and more composable choice.
- **Bounded traversal and result caps** stated as a safety property, not an implementation detail.
- **stdio JSON-RPC without the SDK dependency** keeps local-lite pure; adapter later. Right call.

Suggestions:

1. **Don't catalog unbacked tools** (`find_data_lineage`, `find_config_dependencies` "return whatever extractors have populated") — an agent that calls a documented tool and always gets `[]` learns to distrust the whole server. Register tools dynamically based on which edge types exist in the index; the docs should describe that behavior.
2. **`search_code` and `find_symbol` overlap with what agents already do well** (grep/glob). The genuinely differentiating tools are `impact_analysis`, `retrieve_edit_context`, `get_callers`, `find_tests_for_symbol`. Consider ordering the catalog by differentiation and saying which tools replace vs. augment an agent's native abilities — this is also the pitch.
3. **Add a `stale`/`index_age` field to the envelope.** An agent should know it's reasoning over an index from before the last three commits. This is cheap (compare HEAD to indexed SHA) and prevents the worst failure mode: confidently correct answers about old code.
4. **Uncertainty in the envelope** (per §5.1): `signals` and `confidence` exist; add `resolution: "resolved" | "candidate"` on relationship-derived hits so agents can treat name-match callers differently from import-traced callers.

---

## 8. Roadmap (`roadmap.md`)

- **Phase sequencing is right**: schema/graph/retrieval → MCP → dashboard → non-semantic extractors → legacy. Each phase's "ends with working, tested software" and the per-phase definition-of-done (CI without Docker/network, optional-backend markers, provenance preserved) are excellent discipline — *when honored*.
- **Status marks must be earned.** Phases 1–3 are marked "✅ (this repo)" while (at review time) retrieval, analysis, MCP, API, CLI, and all tests were absent. For a concept document this is the most damaging kind of error: it converts an inspiring roadmap into a credibility liability. Mark reality (⏳/🚧), or split "designed" from "delivered."
- **Phase 5 (COBOL/JCL/CICS) is a different product for a different buyer.** Legacy modernization is enterprise consulting-ware with sales cycles and compliance requirements — nearly disjoint from the local-first OSS dev-tool audience of phases 1–4. Keeping the schema types costs little; putting mainframe parsing on the same roadmap costs focus and invites "unpopulated taxonomy" distrust (§4.1). Recommend moving Phase 5 to a separate "future directions" note.
- **Missing roadmap items** that the concept itself implies: retrieval evaluation harness (§5.2), call-resolution improvements (§5.1), incremental cross-file invalidation (§6.1), index staleness/versioning (§4.3, §7.3). These are more load-bearing than the cross-encoder rerank and NL→query planner currently listed as "next."
- One good call worth highlighting: deferring NL→Cypher-style query generation (identified as a hallucination risk in code-graph-rag) in favor of fixed structured tools. Correct prioritization of reliability over flash — on-brand.

---

## 9. Hard problems the docs don't yet address (consolidated)

1. **Call/reference resolution policy under ambiguity** — the determinism claim's Achilles' heel (§5.1).
2. **Cross-file edge invalidation under incremental indexing** (§6.1).
3. **Retrieval quality evaluation** — no benchmark, golden queries, or metrics anywhere (§5.2).
4. **Index staleness contract** — what agents/humans are told when the graph lags HEAD (§7.3).
5. **Schema/extractor versioning** for a persistent, incrementally-updated graph (§4.3).
6. **Concurrency model** between watcher writes and server reads (§6.4).
7. **Scale envelope** for each profile, with numbers (§6.2).
8. **Semantic enrichment strategy** for the human dashboard without (or with optional) LLM (§6.3).
9. **Monorepo / multi-repo semantics** — repo_id exists in the schema, but cross-repo edges (service A calls service B's API) are implied by `SERVICE`/`API_ENDPOINT` types and never designed.
10. **Positioning vs. LSP/SCIP/CodeQL** (§3) — both as competition and as potential high-precision edge sources.

---

## 10. Recommendations, prioritized

**Sharpen the claims (documentation-only, do first)**
1. Rewrite the determinism language: graph answers are *exact over extracted facts*; extraction itself has stated precision limits per language. Introduce resolved-vs-candidate edges into the concept.
2. Fix roadmap status marks; split "designed" from "delivered."
3. Add the LSP/SCIP/Glean/CodeQL positioning section; adopt the differentiator framing from §3.
4. Tier the schema (populated / planned / aspirational); stop cataloging MCP tools with no backing edges.

**Close the design gaps (concept work)**
5. Write the call-resolution & uncertainty section (§5.1) — this decides whether impact analysis is trustworthy.
6. Write the two-phase extract/resolve incremental design (§6.1).
7. Define the risk-score formula or demote it to factor lists (§5.3).
8. Add index-staleness to the MCP envelope contract (§7.3).

**Add the missing infrastructure concepts**
9. Retrieval eval harness with golden queries in CI — make it part of the definition-of-done (§5.2).
10. State scale envelopes per profile; benchmark on one large OSS repo (§6.2).
11. Decide the dashboard-semantics strategy: structural-only or optional LLM-enrichment adapter (§6.3).
12. Move Phase 5 (mainframe) to a future-directions appendix (§8).

---

## 11. Closing assessment

| Dimension | Rating | Notes |
| --- | --- | --- |
| Core thesis (graph-first, embeddings-as-signal) | ★★★★★ | Correct diagnosis, correct corrective, well-timed for agent tooling |
| Synthesis of prior art | ★★★★☆ | Excellent extraction of the right ideas from three projects; frame too small (no LSP/SCIP/Glean) |
| Schema design | ★★★★☆ | Sound physical design and robustness posture; breadth ahead of demand, no versioning story |
| Retrieval design | ★★★☆☆ | Right fusion machinery and flagship queries; determinism oversold, zero evaluation story |
| Agent interface (MCP) design | ★★★★★ | The explainable envelope + read-only posture is best-in-class |
| Human interface concept | ★★★☆☆ | Inherits a great vision; hasn't confronted the no-LLM semantics gap |
| Roadmap credibility | ★★☆☆☆ | Good sequencing and DoD discipline undermined by unearned checkmarks and Phase-5 scope creep |
| Overall concept | ★★★★☆ | A thesis worth building; needs honesty about heuristic limits and the unwritten hard-problem sections |

The idea is fundamentally sound and the design documents are unusually well-crafted. What separates UCI-as-concept from UCI-as-credible-system is not more features — it's confronting the four hard problems (resolution ambiguity, incremental invalidation, evaluation, staleness) in writing, and narrowing every claim to what the design can actually guarantee. A system whose motto is "explainability over flash" should apply that motto to its own documentation first.
