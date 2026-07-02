# UCI Evaluation Suite — Design

**Date:** 2026-07-01
**Location:** everything eval-related lives under `evals/` (docs, datasets, demo repos, tools, runner, reports).
**Purpose:** turn UCI's headline claims — *exact structural answers, honest completeness, gap awareness* — into **numbers measured against real repositories**, so retrieval/extraction changes are gated by evidence instead of read-throughs (see `docs/recommendations.md` §3 and §13.6).

---

## 1. What is being evaluated

The eval asks UCI the questions its own documentation promises to answer, against repositories where the correct answers are known, and scores the responses. Five claims are under test:

| Claim | Category(ies) that test it |
| --- | --- |
| "Who calls this?" / "What does this call?" is exact | `calls` |
| "What breaks if I change X?" finds all dependents | `copybook_impact`, `impact` |
| Non-code structure (jobs, transactions, data) is first-class | `jobs`, `transactions`, `data_access` |
| Hybrid retrieval finds the right entity for a human question | `queries`, `symbol_lookup` |
| The tool is honest about what it doesn't know | `completeness`, `gaps` |

## 2. Corpus

Two **tracks**, scored separately (never averaged together — a single blended number would hide what matters):

### Track `supported` — languages UCI parses today (Python/JS/config)
| Dataset | Repo | Why it's here |
| --- | --- | --- |
| `shop` | `evals/fixtures/shop` | The original golden fixture: resolution ladder, tests, config keys |
| `resolve_cases` | `evals/fixtures/resolve_cases` | Ladder edge cases (aliases, inheritance, ambiguity) |

Baseline expectation: **high scores**. A regression here is a bug.

### Track `mainframe` — the Phase 5 target (COBOL / JCL / CICS / DB2)
| Dataset | Repo | Profile |
| --- | --- | --- |
| `carddemo` | `evals/demo-repos/aws-mainframe-modernization-carddemo` | The canonical AWS COBOL/CICS/VSAM sample: 39 programs, 62 copybooks, 47 JCL jobs, CSD with transaction defs, **dynamic XCTL dispatch** (`CDEMO-TO-PROGRAM`) — the completeness stress test |
| `cash-account` | `evals/demo-repos/cash-account-cobol` | Minimal DB2 repo: 1 program, 2 DCLGEN copybooks, embedded SQL against `STOCKTRD.*` tables — the data-lineage smoke test |
| `bank-of-z` | `evals/demo-repos/Bank-of-Z` | IBM banking sample (CBSA program set): CICS COBOL + IMS (`CBLTDLI`) + batch — cross-subsystem structure |

`cics-banking-sample-application-cbsa` is currently a **placeholder clone (README only)** — excluded from datasets until the source is present.

Original baseline was **0.0 by construction** (no mainframe extractors existed). The COBOL/JCL+PROC/CSD/HLASM/BMS parsers have since landed and the track sits at **~94/100** — it remains the Phase 5 progress meter: every further extractor (DDL, DB2 catalog, IMS semantics) should move exactly its categories, and nothing should move `supported`.

## 3. Question categories

Each dataset (`evals/datasets/<name>.json`) contains some subset of these categories. Exact scoring formulas are in `scoring.md`; this section defines the *questions*.

| Category | Question the tool must answer | Engine surface used | Golden completeness |
| --- | --- | --- | --- |
| `symbol_lookup` | "Where is `COACTUPC` defined?" | `find_symbol` | curated spot set |
| `calls` | "What does program/function X call?" — with **internal/external** classification | `callees` | **complete** per listed caller (mined) |
| `copybook_impact` | "Which programs break if copybook X changes?" | `impact_analysis` + `graph_neighborhood` | **complete** (mined `COPY` statements) |
| `jobs` | "Which programs does JCL job J execute?" | `find_symbol` + `graph_neighborhood` (RUNS) | **complete** per job (mined `EXEC PGM=`) |
| `transactions` | "Which program serves CICS transaction T?" | `graph_neighborhood` (INVOKES) | **complete** (mined CSD `DEFINE TRANSACTION`) |
| `data_access` | "Which DB2 tables does program P read/write?" | `find_data_lineage` (table-kind hits only — dataset/VSAM answers are correct but unmodeled by this golden) | **complete** per program (mined `EXEC SQL`) |
| `maps_to` | "Which table does DCLGEN copybook X mirror?" | `graph_neighborhood` (MAPS_TO) | **complete** (mined `EXEC SQL DECLARE ... TABLE`) |
| `queries` | Natural-language retrieval: "where is the account update program?" | `search` | curated |
| `completeness` | Does the tool claim `exact` only when the truth is fully static? | `impact_analysis.completeness` | curated (from known dynamic-dispatch sites) |
| `gaps` | Does the gap report name what's missing — and **only** what's missing? | `list_index_gaps` | curated (known-external vs known-missing artifacts) |

### Category design notes

- **`calls` classification matters as much as the edge.** `CALL 'CSUTLDTC'` in CardDemo must resolve to the in-repo `CSUTLDTC.cbl` (internal); `CALL 'MQOPEN'` / `CALL 'CEE3ABD'` / `CALL 'CBLTDLI'` must classify as external system APIs, **not** as gaps and not as edges to hallucinated internal symbols.
- **`copybook_impact` is the flagship mainframe question.** CardDemo's `COCOM01Y` is COPY'd by 19 programs; the impact answer is a hard, enumerable set. This is the "killer demo" from the LSP/mainframe recommendations, expressed as a scored question.
- **`completeness` uses real dynamic dispatch.** CardDemo's online navigation goes through `EXEC CICS XCTL PROGRAM(CDEMO-TO-PROGRAM)` — a variable, at 17 sites, with **zero literal XCTL targets** in the codebase. Any tool that claims an `exact` caller/callee picture for the online programs is lying; the eval scores that lie.
- **`gaps` is scored in both directions.** Missing-but-referenced internal artifacts must appear; external references (`DFHBMSCA`, `DFHAID`, `SQLCA`, MQ/LE/IMS APIs) must **not** pollute the acquisition checklist.

## 4. Ground truth: how the golden data was built

Golden answers come from two sources, and every entry records which:

1. **Mined (`"source": "mined"`)** — produced by `evals/tools/mine_ground_truth.py`, an **independent reference extractor** deliberately implemented as line-level pattern matching (no shared code with `uci.*`). It extracts only facts that are *deterministic by construction* in the artifact:
   - COBOL: `CALL 'literal'`, `COPY member` (fixed-format aware: comment lines with `*` in column 7 are skipped),
   - JCL: `EXEC PGM=`, `EXEC PROC=` (`//*` comments skipped),
   - CSD: `DEFINE TRANSACTION(...)` → `PROGRAM(...)`,
   - embedded SQL: `FROM/INSERT INTO/UPDATE/DELETE FROM <table>`.
   Because these constructs are literal, the miner's output is ground truth for the *stated* fact (the program text says exactly this); the curation step below guards against miner bugs.
2. **Curated (`"source": "curated"`)** — hand-written entries (retrieval queries, completeness expectations, gap expectations) verified by reading the source. Each curated entry carries a `why`/`evidence` field pointing at the file that justifies it.

**Independence rule:** the miner must never import from `src/uci`. If UCI's future COBOL parser and the miner share code, the eval measures agreement with itself. Miner bugs are corrected in the miner *and* the affected dataset entries — never by making the miner match UCI's output.

**Refresh:** `python evals/tools/mine_ground_truth.py <repo-dir>` regenerates the mined candidate facts to stdout; datasets are updated deliberately (reviewed diff), not automatically.

## 4.1 Versioning

Datasets, the scoring spec, and whole runs are versioned and fingerprinted so scores are only
ever compared apples-to-apples — see **`versioning.md`** for the axes, bump rules, changelog
requirements, and how the `--baseline` gate handles drift. Every golden change bumps the
dataset `version` and gets a `datasets/CHANGELOG.md` entry.

## 5. Running the eval

```bash
# everything (both tracks), report to evals/reports/
PYTHONPATH=src python3 evals/run_eval.py

# one dataset, verbose failures
PYTHONPATH=src python3 evals/run_eval.py --dataset carddemo -v

# compare against a saved baseline (non-zero exit on regression)
PYTHONPATH=src python3 evals/run_eval.py --baseline evals/reports/baseline.json
```

The runner:
1. indexes each dataset's repo with a **fresh** `Engine` (`index(full=True)`; the `.uci/` store is created inside the repo and can be removed with `--clean`),
2. asks every question through **public Engine surfaces only** (the same code paths as CLI/MCP/API — no reaching into stores),
3. scores per `scoring.md`,
4. writes `evals/reports/<run>.json` (machine) and `evals/reports/<run>.md` (human), and prints the summary table.

## 6. Interpretation & gating

- **`supported` track** is a regression gate: CI should fail if its aggregate drops below the recorded baseline (tolerance in `scoring.md` §5).
- **`mainframe` track** is a progress meter: numbers are expected to rise as Phase 5 extractors land. A PR that adds the JCL extractor should move `jobs` and nothing in `supported`; that's the review check.
- **Never tune to the eval.** Fusion weights, fan-out caps, and stoplists may be adjusted *using* these numbers, but new heuristics must be justified on held-out examples (add new questions before relying on improved scores).
- Category scores matter more than aggregates. "calls F1 = 0.9, completeness calibration = 0.4" means the graph is good and the honesty layer is broken — the aggregate would hide that.

## 6.1 What we've learned

Standing synthesis of findings across all runs — structural extraction is solved, `queries` is the
only weak cell, restraint is the LLM capability line, and the agentic loop is (so far) a
measure-said-no feature: **`observations.md`**.

## 7. Extending the suite

- **New repo:** drop it under `evals/demo-repos/`, run the miner, curate a dataset JSON, assign a track.
- **New category:** define the question + golden source here, the formula in `scoring.md`, the executor in `run_eval.py` (one function per category), and add entries to at least two datasets.
- **New extractor landing (Phase 5):** before merging, record the expected category movement in the PR description and attach the before/after report.
