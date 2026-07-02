# UCI Evaluation — Scoring System

**Scoring spec version: 1.0** (mirrored by `SCORING_VERSION` in `run_eval.py`; bump both together — see `versioning.md`).

Companion to `evaluation.md`. This document is the **exact** contract: every number in an eval report is defined here, and the implementation in `evals/run_eval.py` must match this document (when they disagree, this document wins and the runner is fixed).

---

## 1. Matching rules (applied before any metric)

- **Names are compared case-insensitively** on the *simple name* (mainframe convention: `COACTUPC` ≡ `coactupc`; Python: last dotted segment). Where a dataset entry gives a dotted/qualified name, a tool answer matches if its `qualified_name` equals it case-insensitively, **or** its simple name equals the entry's simple name *and* the entry is marked `"loose": true`.
- **Paths** are compared repo-relative with forward slashes, exact match.
- **Tables** (`data_access`) match on the qualified table name (`STOCKTRD.CASHACCOUNTY`) case-insensitively; an unqualified tool answer matches its qualified golden if the last segment equals.
- Duplicate answers are deduplicated before scoring (returning the same caller five times earns it once).
- A category listed in a dataset but returning an engine error for **every** item scores 0 for that dataset (errors are not skips).

## 2. Per-category metrics

Every category yields a score in **[0, 1]**.

### 2.1 `symbol_lookup` — accuracy@1
For each entry `{name, path}`: query `find_symbol(name)`; the entry scores 1 if the **first** result's path equals `path`, else 0.
`score = mean(entries)`

### 2.2 `calls` — set F1 per caller, classification-checked
Entries are grouped by `from`. For each caller with a **complete** golden set (`internal` targets it calls):
- `answered` = deduped simple names of `callees(from)` results.
- `TP = |answered ∩ golden_internal|`, `FP = |answered − golden_internal − golden_external|`, `FN = |golden_internal − answered|`.
- Targets in `golden_external` are **neutral** in F1 (reporting them as callees is acceptable) **but**: an external target reported with `missing: true`, or appearing in the gap report, counts as one FP (external misclassified as missing).
- `caller_score = F1 = 2TP / (2TP + FP + FN)` (0 if denominator 0 and golden non-empty; 1 if both golden and answered are empty).
- Entries may carry `"expect_resolved": true` (supported track): a matching edge whose `resolution` is **not** in `RESOLVED_LEVELS` earns 0.5 TP instead of 1 (edge found, ladder label wrong).
- **Vacuity rule:** when goldens are derived from mined facts, callers with an empty `internal` set are excluded — an unindexed repo answering nothing against an empty golden would earn free 1.0s and inflate the track.
`score = mean(caller_scores)`

### 2.3 `copybook_impact` — set F1 per copybook
For each `{copybook, dependents}` (golden complete, mined from `COPY` statements):
- `answered` = union of (a) `impact_analysis(copybook)` callers (resolved + candidates), (b) programs adjacent to the copybook's entity in `graph_neighborhood(depth=1)` via incoming edges of any type.
- F1 against `dependents` as in 2.2 (no neutral class).
`score = mean(copybook F1)`

### 2.4 `jobs` — set F1 per job, internal only
For each `{job, programs_internal, programs_external}`:
- `answered` = program names reached from the job's entity via `RUNS`/`CALLS` edges in `graph_neighborhood(depth=1)`.
- F1 computed against `programs_internal`; `programs_external` (system utilities: `IDCAMS`, `SORT`, `IEBGENER`, …) are neutral, with the same misclassification penalty as 2.2.
`score = mean(job F1)`

### 2.5 `transactions` — accuracy
For each `{tran, program}`: 1 if the transaction's entity has an `INVOKES` (or `HANDLES`) edge to `program` in `graph_neighborhood(depth=1)`, else 0.
`score = mean(entries)`

### 2.6 `data_access` — mode-weighted F1 per program
For each `{program, reads, writes}` (golden complete, mined from `EXEC SQL`):
- From `find_data_lineage(program)`: `answered_reads` (hits whose reason/edge is READS), `answered_writes` (WRITES). A table answered with the **wrong mode only** counts 0.5 TP instead of 1 (edge found, semantics wrong).
- `F1_reads`, `F1_writes` computed separately (skip a mode whose golden set is empty); `program_score = mean(present modes)`.
`score = mean(program scores)`

### 2.10 `maps_to` — DCLGEN lineage recall
For each `{copybook, tables}` (golden complete, mined from `EXEC SQL DECLARE <table> TABLE` in `.cpy` members): the copybook's entity must carry an outgoing `MAPS_TO` edge to each declared table (matched on qualified name, or last segment). `entry_score = matched / |tables|`; `score = mean(entries)`. Weight 1.5 (data family).

### 2.7 `queries` — retrieval quality
For each `{q, expected, k=5}` (any expected entity counts as relevant):
- `hit_rank` = rank (1-based) of the first result matching any expected name; ∞ if none in top `k`.
- `RR = 1/hit_rank` (0 if ∞); `recall@k` = |expected ∩ top-k| / |expected|.
- `entry_score = 0.5·RR + 0.5·recall@k`
`score = mean(entries)` — report also raw `MRR` and mean `recall@k` for diagnostics.

### 2.8 `completeness` — calibration accuracy (the honesty score)
For each `{symbol, expect_exact}`: query `impact_analysis(symbol)`; `claimed_exact = (completeness.level == "exact")`.
- Correct claim → 1. **Claiming `exact` when `expect_exact=false` → 0** (the dangerous lie).
- Claiming non-exact when the truth is fully static (`expect_exact=true`) → **0.5** (over-hedging: annoying, not dangerous — asymmetry is deliberate).
`score = mean(entries)`
*Note:* when the target symbol doesn't exist in the index at all (e.g. COBOL not yet parsed), entries score 0 — an unanswerable impact question is not an honest one.

### 2.9 `gaps` — noise-free recall
Given `{must_report_missing, must_not_report_missing}` and the tool's gap list `G` (names, deduped):
- `recall = |must_report_missing ∩ G| / |must_report_missing|` (define as 1 if the golden set is empty),
- `noise = |must_not_report_missing ∩ G| / |must_not_report_missing|` (0 if golden set empty),
- `score = recall · (1 − noise)`
The product (not mean) is deliberate: an acquisition checklist that names the right artifacts **buried in system-API noise** is operationally useless.
- **Vacuity rule:** if `must_report_missing` is empty **and** the tool reported zero gaps, the category is marked `vacuous` and **excluded from the dataset aggregate** (§3.1) — an empty index must not earn honesty points for silence. The noise check becomes live as soon as the tool reports any gap.

## 3. Aggregation

### 3.1 Dataset score
Weighted mean over categories **present in the dataset**:

| Category | Weight | Rationale |
| --- | --- | --- |
| `calls`, `copybook_impact` | 2.0 | The core structural claims |
| `jobs`, `transactions`, `data_access` | 1.5 | The beyond-code differentiator |
| `completeness`, `gaps` | 1.5 | The honesty contract |
| `symbol_lookup`, `queries` | 1.0 | Table stakes |

`dataset_score = Σ(wᵢ·scoreᵢ) / Σwᵢ` — reported as 0–100. Categories marked `vacuous` (§2.2, §2.9) are shown in the report but excluded from this mean.

The `impact` category (supported track: `{symbol, callers, tests, config}` triplets) scores the mean of the three set-F1s per entry, weight 2.0 — it is the Python analog of `copybook_impact`.

### 3.2 Track score
`track_score = mean(dataset scores in track)` — `supported` and `mainframe` are **never combined** into one number. Reports show both, plus every category cell (dataset × category matrix).

### 3.3 Capability coverage (mainframe track only)
`coverage = fraction of golden items whose *entities* exist in the index at all` (e.g. the copybook has an entity, regardless of edge quality). Separates "can't see the files" (parser missing) from "sees them, wrong edges" (extractor quality) — the two look identical as F1=0 but need different work.

## 4. Report format

`evals/reports/<run>.json`:
```jsonc
{
  "run": "2026-07-01T12:00:00Z", "git_sha": "…", "uci_version": "0.1.0",
  "tracks": {
    "supported": {"score": 87.4, "datasets": {"shop": {"score": 91.0, "categories": {"calls": {"score": 1.0, "details": [...]}, ...}}}},
    "mainframe": {"score": 3.1, "coverage": 0.0, "datasets": {...}}
  }
}
```
`<run>.md` renders the dataset × category matrix plus per-item failures (golden vs answered diff) for anything below 1.0.

## 5. Regression gating

With `--baseline <report.json>`:
- **`supported` track:** fail (exit 1) if the track score drops > **1.0 point**, or any category score drops > **0.05** absolute.
- **`mainframe` track:** never fails the gate (progress meter), but the delta is printed.
- The committed baseline (`evals/reports/baseline.json`) is updated only in a PR that explains the movement.

## 6. Known limitations (accepted)

1. Mined golden sets are complete **for literal constructs only** — dynamic calls (`CALL WS-PGM`, `XCTL PROGRAM(var)`) are represented in `completeness` expectations, not as edges. A future dataflow-resolved golden tier can upgrade them.
2. `queries` goldens encode one curator's judgment of relevance; scores below ~0.9 there are signals, not verdicts.
3. Fan-in counts in copybook goldens count *programs*, not COPY sites (a program copying twice counts once) — matching how impact consumers think.
4. The eval indexes repos in place (`.uci/` inside each demo repo); `--clean` removes the stores. Demo repos must stay read-only otherwise.
5. **Anonymous dynamic dispatch:** a variable-target call site (`XCTL PROGRAM(CDEMO-TO-PROGRAM)`) names no target, so it cannot be matched to any specific symbol's caller list — strictly, one such site anywhere makes *every* program's caller-completeness unknowable. The completeness contract deliberately scopes the claim to (candidate edges + unresolved sites *naming* the target + the target's own dynamic sites); `completeness` goldens are written against that contract, not against metaphysical completeness. A future dataflow tier (tracing `MOVE 'X' TO var`) can tighten it.
