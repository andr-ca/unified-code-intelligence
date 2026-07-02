# Recommendations — Implementation Status

Maps each item in [`recommendations.md`](recommendations.md) (and the concept gaps in
[`feedback.md`](feedback.md)) to what was implemented. Legend: ✅ done · 🚧 partial/scaffolded ·
⏳ planned (roadmap).

## 1. Determinism & call-graph honesty (P1/P2)

| § | Recommendation | Status | Where |
| --- | --- | --- | --- |
| 1.2 | Resolution ladder; confidence derived from level | ✅ | `ingest/graph_builder.py` `_resolve_callee`; levels `syntactic/import-traced/inherited/inferred/name-match/candidate` |
| 1.3 | Import binding table (`from x import y as z` → `z`→`x.y`); make imports the backbone | ✅ | `parser/base.py` `ParsedImport.binds` + `resolve_js_module`; `python_parser`/`javascript_parser` populate binds; `graph_builder` consults them first |
| 1.4 | Receiver-aware narrowing (`self`/`Cls.m`/`alias.m`); fan-out cap + stoplist; R4/R5 never drive multi-hop | ✅ | `graph_builder` (receiver + `_FANOUT_CAP=5` drop→unresolved); multi-hop gating in `engine._call_graph` + `retrieval/hybrid._graph_signal` (`RESOLVED_LEVELS`) |
| 1.5 | Stratified impact pack (`resolved`/`candidates`/`unresolved`) + computed `completeness` | ✅ | `retrieval/impact.py` `analyze` + `_stratify` + `_completeness` |
| 1.6 | Record `unresolved_call` facts | ✅ | `graph_builder.unresolved_calls` → persisted to state; surfaced in impact `callers.unresolved` |
| 1.7 | Optional LSP bridge to promote/prune edges | ⏳ | roadmap Phase 2 (`extractor="lsp-*", resolution="inferred"` slot ready) |
| 1.8 | Reword the determinism promise | ✅ | `architecture.md` §6, `retrieval-strategy.md` §8–9 |

## 2. Incremental indexing without graph rot (P4)

| § | Recommendation | Status | Notes |
| --- | --- | --- | --- |
| 2 | Two-phase extract/resolve + frontier invalidation | 🚧/⏳ | MVP re-parses all files and **fully rebuilds** the repo graph each pass, so cross-file edges cannot go stale (no rot). The two-phase optimization for scale is roadmapped. |
| 2.3 | Index generations + HEAD SHA; readers use last completed generation | ✅ | `indexer` stamps `state["index"] = {generation, head_sha, indexed_at}` |
| 2.4 | `--full` escape hatch + auto full-reindex on model change | ✅ | `uci index --full`; embedding-model change clears vectors and re-embeds (`indexer` §6.2 guard) |

## 3. Evaluation harness (P1/P2/P7)

| Status | Notes |
| --- | --- |
| ⏳ next | Golden call-graph + retrieval + impact fixtures with precision/recall **per resolution level** and MRR@k. This is the agreed next task; the substrate (resolution labels, `unresolved_calls`, completeness) is now in place to measure it. |

## 4. Explainability of UCI's own outputs (P5)

| § | Recommendation | Status | Where |
| --- | --- | --- | --- |
| 4.1 | Publish the risk formula / return factors | ✅ | `retrieval-strategy.md` §4 documents the formula; impact returns `risk.factors` |
| 4.2 | Truncation visible | ✅ | `engine.graph_neighborhood` returns `truncated`/`limit` |
| 4.3 | Staleness visible (`generation, head_sha, commits_behind`) | ✅ | `engine._index_status` added to `search`/`impact` envelopes |

## 5. Secrets — redact at ingest (P6)

| § | Recommendation | Status | Where |
| --- | --- | --- | --- |
| 5.1 | Never chunk/embed config-language files | ✅ | `chunking.build_chunks` guards `language == "config"`; indexer only chunks code files |
| 5.2 | Entropy/pattern secret scrub for code chunks | ✅ | `chunking.scrub_secrets` (AWS keys, PEM, `key=value` credentials) |
| 5.3 | State the trust boundary in docs | ✅ | `architecture.md` §8 + `README` (treat `.uci/` like the repo) |

## 6. Degradation & mixed models (P7)

| § | Recommendation | Status | Where |
| --- | --- | --- | --- |
| 6.1 | Rename hash "semantic" → `lexical-hash` | ✅ | provider `signal_name`; `hybrid` uses it; envelope `stats.semantic_signal` |
| 6.2 | Guard mixed-model vector stores | ✅ | `indexer` stores `embedding_meta`; on change clears vectors + re-embeds |

## 7. One graph, two audiences (P8)

| § | Recommendation | Status | Notes |
| --- | --- | --- | --- |
| 7 | Pick a semantics strategy | ✅ (a) + ⏳ (b) | Chose **structural-honest** dashboard now (all views derive from graph facts; onboarding is a dependency-ordered reading path via topo/heuristic — no LLM). Optional **LLM-enrichment adapter** (`extractor="llm:*", confidence<1.0`) is roadmapped and fits the provenance machinery. |

## 8. Documentation & positioning

| § | Recommendation | Status | Where |
| --- | --- | --- | --- |
| 8.1 | Re-mark roadmap ✅/🚧/⏳ | ✅ | `roadmap.md` legend + status column |
| 8.2 | Reworded determinism claim everywhere | ✅ | architecture/retrieval docs |
| 8.3 | Competitive section (LSP/SCIP/Glean/CodeQL) | ✅ | `repo-comparison.md` §7 |
| 8.4 | Tier schema; register MCP tools dynamically | ✅ | `canonical-schema.md` tier legend; `engine.capabilities()` + `mcp.tools.list_tools(engine)` annotate `available` |
| 8.5 | State scale envelopes per profile | ⏳ | after the eval harness benchmark |

## 9. Priority-order (top 5) — all done
1. Resolution ladder + confidence policy ✅  2. Stratified impact + completeness ✅
3. Golden fixtures + precision/recall ⏳ (next)  4. Frontier-safe incremental 🚧 (full-rebuild is correct; two-phase ⏳)
5. Config-content exclusion ✅

## 10. Gap Registry (recommendations.md §10 / `next-iteration-gap-registry.md`)  ✅

| Spec item | Status | Where |
| --- | --- | --- |
| Placeholder (stub) entities with `__missing__` id segment, `missing=true`, self-healing | ✅ | `graph_builder.report_gap` + `MISSING_SEGMENT`; edges carry `resolution="missing"` |
| `gaps` store + `report_gap()` convention | ✅ | `store/sqlite_backend.py` (`gaps` table + methods), `MetadataStore` interface; wired into import resolution |
| External-vs-missing classifier (stdlib/vendor/prefixes) | ✅ | `graph_builder._is_external_module` (stdlib + internal-tops + `UCI_GAP_EXTERNAL_PREFIXES`) |
| `uci gaps` CLI + `list_index_gaps` MCP tool | ✅ | `cli/main.py cmd_gaps`; `mcp/tools.py` |
| `completeness.gaps` citations in impact packs | ✅ | `retrieval/impact.py _gaps_for` |
| Dashboard gaps panel + dashed stub nodes | ✅ | `api/views.gaps_page` + nav; `static/app.js` dashed rendering |
| Generation-based auto-close | ✅ | `indexer` clears + re-writes gaps each pass (full-rebuild) |
| Call/COBOL/JCL extractors adopt the convention | ⏳ | `report_gap` is the shared entry point; wired for imports now, others as extractors land |

Acceptance criteria (all verified by `tests/test_gaps.py`): removing a fixture file yields a gap naming
it + expected path + referencing sites; restoring heals + auto-closes; impact cites `completeness.gaps`;
stdlib/vendor imports produce zero gaps.

## 11. Phase 3 — Post-implementation review fixes (recommendations.md §11)  ✅

Every finding from the §11 audit addressed:

| Finding | Fix | Where |
| --- | --- | --- |
| 11.1(1) Ambiguous import candidates mislabeled `import-traced` (resolved) | Now `candidate`, `conf min(0.4, 1/N)`, excluded from `RESOLVED_LEVELS` | `graph_builder._resolve_callee` |
| 11.1(2) Speculative edges seeded multi-hop via frontier | Node only added to frontier when the reaching edge is resolved | `engine._call_graph` |
| 11.1(3) Tool-count test literal / false "106 pass" | Test counts from `TOOL_SPECS`; status now says 121 | `tests/test_api.py`, this doc |
| 11.2(4) Inheritance edges had no `resolution`, global first-match, conf 1.0 | `_resolve_type_ref` ladder (binds/module narrowing) labels EXTENDS/IMPLEMENTS/REFERENCES; `_ancestor_map` uses only resolved edges | `graph_builder` |
| 11.2(5) `unresolved_call` captured only fan-out-capped | Also records `not-found` (non-builtin bare) and `dynamic-receiver`, filtering self/binds/builtins | `graph_builder._unresolved_reason` |
| 11.2(6) R2 `inferred` trusted global bare-name class | `_narrowed_class` narrows by caller module/imports; ambiguous → falls through to candidate | `graph_builder` |
| 11.2(7) `get_callers` completeness ignored unresolved sites | `_call_graph` consults `_unresolved_naming` (callers direction) | `engine` |
| 11.3(8) `mcp-tools.md` stale (10 tools) | Catalog + prose updated for 11 tools + `available` annotation | `docs/mcp-tools.md` |
| 11.3(9) Weights not read from env | `UCI_WEIGHT_*` / `UCI_RRF_K` wired + `.env.example` | `config.py` |
| 11.3(10) "re-indexes only what changed" unqualified | Principle reworded (graph=full-rebuild, embeddings=hash-incremental) | `docs/architecture.md` |
| 11.4(11) Stray `onboarding_init_placeholder.py` | Deleted | — |
| 11.4(12) `find_symbol(exact=True)` fuzzy fallback | Returns empty on no exact match | `engine.find_symbol` |
| 11.4(13) O(n) keyword signal / FTS5 unimplemented | Documented as a scaling item (roadmap) | `docs/retrieval-strategy.md` |

**Phase 3 exit criteria met** (all in `tests/test_resolution.py`): no unresolved-labeled edge appears
in a `resolved` stratum; no multi-hop path crosses a speculative edge; a dynamic caller keeps
`completeness` off `exact`; inheritance edges carry resolution and ambiguous base names degrade below
`RESOLVED_LEVELS`; the tool-count test counts from `TOOL_SPECS`; full suite green.

## 12. Phase 4 — Second-audit residuals (recommendations.md §12)  ✅

| Finding | Fix | Where |
| --- | --- | --- |
| 12.1 Aliased base classes dead-ended before the binding table | `_resolve_type_ref` checks `binds` **first**, then name candidates | `graph_builder` |
| 12.2 Unresolved type references vanished silently | `_maybe_ref_gap` records a `class` gap + missing stub edge when the base traces to an internal module; external bases → external stub | `graph_builder` |
| 12.3 `get_callees` completeness ignored hidden callees | `_call_graph` consults `_unresolved_from` (caller == target) for the "out" direction | `engine` |
| 12.4 Stub entities entered agent surfaces unlabeled | `RetrievalHit`/`_entity_hit`/graph nodes carry `missing`/`external`; `search`/`find_symbol` exclude stubs | `retrieval/types.py`, `engine`, `hybrid` |
| 12.5 Call-target gaps / FTS5 / global-name over-report | Tracked (no action) — scoped to Phase 4 extractors and the eval harness | roadmap |

**Phase 4 exit criteria met** (`tests/test_resolution.py`, `tests/test_gaps.py`): aliased base →
`import-traced` EXTENDS; unindexed internal base → gap + non-`exact` completeness; `get_callees` on a
dynamic function → `partial` with `unresolved_sites > 0`; stub hits are labeled `missing:true` and never
reach search/find_symbol unlabeled.

## 13. Phase 5 — Third-audit residuals (recommendations.md §13)  ✅

| Finding | Fix | Where |
| --- | --- | --- |
| 13.1 Impact pack callees had the hidden-callee blind spot | `analyze` adds `callees.unresolved` (via `_unresolved_from`) and folds it into `_completeness`, so `impact_analysis` matches `get_callees` | `retrieval/impact.py` |
| 13.2 A binds miss fell through to a same-named global | Both `_resolve_type_ref` (return None) and the new `_resolve_via_binds`/`_emit_bound_call` short-circuit a bound-but-unindexed target to a gap + stub edge — never an unrelated global | `graph_builder` |
| 13.3 External-stub edges were labeled `resolution="missing"` | External bases/calls now carry `resolution="external"`; a test asserts no `missing` edge points at an `external` entity | `graph_builder` |
| 13.4 Untraceable refs / call-target-gaps / FTS5 | Recorded decisions (no action) | roadmap |

**Phase 5 exit criteria met** (`tests/test_resolution.py`): `impact_analysis` reports
`callees.unresolved.count > 0` matching `get_callees`; `from pkg.missing import Thing; class C(Thing)`
yields a gap (not an edge to an unrelated `Thing`) — same for a called function; external-stub edges
are `resolution="external"` and no `missing` edge points at an external entity.

## Test coverage of the above
`tests/test_resolution.py` (ladder, import-traced, inferred, fan-out→unresolved, **Phase-3**: ambiguous
downgrade, no-speculative-multihop, inheritance resolution, dynamic-dispatch completeness, exact
find_symbol, env weights), `tests/test_impact.py` (stratification, completeness, staleness),
`tests/test_chunking.py` (config exclusion, secret scrub), `tests/test_retrieval.py` (lexical-hash label,
staleness), `tests/test_mcp.py` (dynamic availability), `tests/test_gaps.py` (gap registry, **Phase-4**: internal
base-class gaps, stub labeling/exclusion), plus **Phase-5** resolution tests (impact/get_callees parity,
binds-miss gapping, external-stub labeling).
**129 tests pass, no Docker/network.**
