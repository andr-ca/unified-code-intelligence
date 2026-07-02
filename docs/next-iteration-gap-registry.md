# Next Iteration — Gap Registry ("Known Unknowns" Manifest)

**Date:** 2026-07-01
**Status:** ✅ **Implemented** in this repository (imports produce gaps; stubs self-heal; `uci gaps`,
`list_index_gaps` MCP tool, Gaps dashboard page, and `completeness.gaps` citations all shipped). The
call/COBOL/JCL extractors listed in §4 adopt the same `report_gap` convention as they are written.
See `docs/recommendations-status.md` §10 for the per-item status and `tests/test_gaps.py` for coverage.
**One-line summary:** Never discard a resolution failure. Every "couldn't resolve X" becomes a first-class fact that names what is missing, points at every site that needs it, and rolls up into a prioritized acquisition checklist for developers.

---

## 1. Problem statement

Today (and in the resolution-ladder design), when an extractor fails to resolve something — a copybook member, a called program, a Python import, a JCL PROC — the failure is either silently dropped or, at best, counted. But **at the moment of failure, the extractor almost always knows the exact name of the missing artifact**, and frequently its expected location. Discarding that is discarding the most actionable output an indexer can produce for a partially-available codebase.

This matters most in exactly UCI's target scenarios:

- **Legacy/mainframe estates**, where source arrives incrementally (copybooks live in one library, PROCs in another, some programs only exist in Endevor) and "what source are we still missing?" is literally a billable deliverable of a modernization engagement.
- **Agent workflows**, where an impact analysis that is silently incomplete misleads, but one that says *"12 callers reference programs not in the index — here are their names"* lets the agent (or human) act correctly.

## 2. Goals / non-goals

**Goals**
1. Every resolution failure produces a durable, queryable **gap record** identifying what is missing, why, and every referencing site.
2. Dangling edges point at **placeholder entities** instead of being dropped, so the graph stays connected and self-heals when the artifact arrives.
3. Developers get a **ranked acquisition report** (`uci gaps`) — "obtaining copybook `PAYROLL01` resolves 240 dangling references."
4. Agents get the same via MCP (`list_index_gaps`), and `completeness` fields cite specific gaps instead of a vague "partial."

**Non-goals**
- Fetching missing artifacts automatically (report only; acquisition is a human/agent decision).
- Treating external dependencies (stdlib, vendor packages, system modules) as gaps — see §6.

---

## 3. Design

### 3.1 Placeholder ("stub") entities

When an edge's target cannot be resolved to an indexed entity, the normalizer creates a stub node instead of dropping the edge:

```python
Entity(
    id="legacy_program:{repo}:__missing__:PGMX",   # reserved path segment "__missing__"
    kind=EntityType.LEGACY_PROGRAM,                # best-known kind from the call form
    name="PGMX",
    qualified_name="PGMX",
    provenance=Provenance(repo_id=..., path="", extractor="normalizer", confidence=0.0),
    attributes={"missing": True, "expected_origin": "PROD.LOADLIB (from STEPLIB)"},
)
```

Rules:
- Stubs are **idempotent** (same missing name → same id) so N referencing sites converge on one node whose fan-in *is* the priority signal.
- All real edges (`CALLS`, `COPY`/`IMPORTS`, `RUNS`, …) point at the stub with their true provenance and `resolution="missing"` — traversal and impact analysis see them and can label them.
- **Self-healing:** when the artifact is later indexed, the indexer resolves the same deterministic name, replaces the stub (same-id upsert or a stub→real rebind pass in the resolve phase), and every dangling edge heals without touching the referencing files.

### 3.2 Gap records

A dedicated store (new `gaps` table in the SQLite backend; interface method on `MetadataStore`) — one row per missing artifact per repo:

| Field | Example |
| --- | --- |
| `artifact_kind` | `copybook` \| `program` \| `proc` \| `module` \| `dclgen` \| `catalog_extract` \| `map/screen` |
| `name` | `PAYROLL01` |
| `stub_entity_id` | links into the graph |
| `expected_origin` | best-effort: copybook lib from SYSLIB/config, JCLLIB order, expected repo path `pricing/rules.py` |
| `reasons` | `copy-member-not-found`, `call-target-not-indexed`, `import-unresolved`, … |
| `ref_count` / `referencing_sites` | fan-in + list of `(path, line)` provenance of every referencing edge |
| `first_seen` / `last_seen_generation` | lifecycle; gaps not re-observed in the latest generation are auto-closed |

### 3.3 The extractor convention (the actual "refactor")

The code change is small but must become **a rule every extractor follows**: resolution code paths never `continue`/drop on failure — they call one helper:

```python
normalizer.report_gap(kind, name, site_provenance, reason, expected_origin=None)
```

which creates/updates the stub + gap record. Concretely this touches: the (in-progress) resolve phase for calls/imports, the future COBOL `COPY`/`CALL` extractor, JCL `EXEC PGM=`/`PROC=` handling, `EXEC SQL INCLUDE`, and the Python/JS import resolvers. Retrofitting this convention later across ten extractors is painful; establishing it now, while extractors are being written, is nearly free. **This is why it's proposed as the immediate next iteration.**

---

## 4. What each extractor can identify — the per-language capability table

This is the payoff table: what the gap report can literally tell a developer to go obtain.

| Source construct | Missing artifact named in source? | Expected origin derivable? |
| --- | --- | --- |
| COBOL `COPY PAYROLL01` | ✅ member name | often — SYSLIB DD in compile JCL, or configured copybook paths |
| COBOL `CALL 'PGMX'` / `EXEC CICS LINK PROGRAM('PGMX')` | ✅ program name | sometimes — CSD PROGRAM defs, load-lib conventions |
| COBOL `CALL WS-PGM` resolved to literal by dataflow (R2) | ✅ via traced `MOVE` | same as above |
| JCL `EXEC PROC=DAILYPRC` | ✅ PROC name | ✅ JCLLIB ORDER statement |
| JCL `EXEC PGM=X`, `DD DSN=…` | ✅ program / dataset names | STEPLIB / catalog conventions |
| `EXEC SQL INCLUDE SQLDA` / DCLGEN member | ✅ member name | copybook/DCLGEN library config |
| HLASM `COPY member`, macro invocation, `V(EXTSYM)`/`EXTRN` | ✅ member / external symbol | SYSLIB concatenation |
| IMS: PSB named by `APPLCTN`, DBD named by PSB `PCB` | ✅ PSB/DBD names | PSBLIB/DBDLIB |
| BMS `SEND MAP('MAPX')` with no map source | ✅ mapset name | CSD MAPSET defs |
| DB2 program→table ground truth absent | n/a | ✅ "provide `SYSIBM.SYSPACKDEP` unload" — a *procedural* gap, worth one standing record |
| Python `from pricing.rules import X` unresolved | ✅ | ✅ exact expected path `pricing/rules.py` |
| JS `import './lib/util'` unresolved | ✅ | ✅ expected paths incl. `index.*` variants |
| Python/JS bare external import (`numpy`, `react`) | ✅ | **not a gap** — external (§6) |

## 5. Surfaces

1. **CLI — `uci gaps [--kind copybook] [--json]`**: table ranked by `ref_count`, showing name, kind, expected origin, top referencing files. The default human output is the acquisition checklist.
2. **MCP — `list_index_gaps`**: same data in the standard envelope, so an agent can decide "search the repo harder / ask the user for the file / proceed with caveats."
3. **`completeness` integration** *(depends on the in-flight stratified-impact-pack work)*: impact/caller results referencing stub entities add `"gaps": [{name, kind, ref_count}]` to their completeness block — the vague `"partial"` becomes "partial because PGMX and PGMY are not indexed."
4. **Dashboard**: a gaps panel; stub nodes rendered distinctly (dashed outline) in the graph explorer so a human browsing the graph *sees* the frontier of the unknown.

## 6. The missing-vs-external boundary (noise control)

Without this the report drowns in requests for `os.py` and CICS internals. Classification at `report_gap` time:

- **External (stub yes, gap no):** resolvable-by-convention names — Python stdlib (`sys.stdlib_module_names`) and installed/declared packages (pyproject/package.json deps), JS bare specifiers, z/OS system modules (`DFH*` CICS, `DSN*` DB2, `CEE*` LE, `DFS*` IMS prefixes), SQL system objects (`SYSIBM.*`). These become ordinary external-dependency stubs (`attributes.external=true`) — useful graph nodes, excluded from the acquisition report.
- **Missing (stub + gap):** everything else — it *should* exist in the estate.
- Borderline cases default to **missing** with `confidence` on the gap record; a per-repo config list (`UCI_GAP_EXTERNAL_PREFIXES`) lets teams tune. Misclassifying external-as-missing costs a report line; missing-as-external costs silent incompleteness — so the default errs toward reporting.

## 7. Implementation outline & touchpoints

| Step | Where | Size |
| --- | --- | --- |
| 1. `Gap` record + `gaps` table + `MetadataStore` methods | `core/`, `store/sqlite_backend.py` | S |
| 2. Stub-entity helper + reserved `__missing__` id segment + `report_gap()` | normalizer / resolve phase | S |
| 3. Wire into Python/JS import & call resolution (replace silent drops) | resolver | S–M |
| 4. External-vs-missing classifier + config | normalizer, `config.py` | S |
| 5. `uci gaps` CLI + `list_index_gaps` MCP tool | `cli/`, `mcp/` | S |
| 6. Completeness citation of gaps | retrieval/impact (after stratification lands) | S |
| 7. Stub self-healing in the incremental resolve phase + generation-based auto-close | indexer | M |
| 8. Mainframe extractors adopt the convention as they are written | Phase 5 work | — (convention, not code) |

**Acceptance criteria:** (a) indexing a fixture repo with a deliberately removed file yields a gap naming that file, its expected path, and all referencing sites; (b) restoring the file and re-indexing heals every edge and auto-closes the gap; (c) impact analysis on a symbol with stub callers reports them in `completeness.gaps`; (d) stdlib/vendor imports produce zero gap records.

## 8. Why this iteration, in one paragraph

The resolution-ladder work currently in flight decides *how well* UCI resolves what it has. The gap registry decides *what UCI says about what it doesn't have* — and it must land while extractors are still few, because it is a convention, not a feature. It converts UCI's most embarrassing failure mode (silently incomplete answers) into a differentiating product surface: for legacy estates, the ranked "source you still need to obtain" report is plausibly the first artifact a modernization team would pay for; for agents, it is the difference between being misled by the graph and being told exactly where the graph ends.
