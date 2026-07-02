# Observations from the Evaluation Suite

**Date:** 2026-07-02
**Scope:** what we actually learned by running `evals/run_eval.py` (system eval) and
`evals/llm_eval.py` (model benchmark) across development — not aspirations, findings backed by a
number and the run that produced it. This is the standing synthesis; individual scores live in
`evals/reports/`, formulas in `scoring.md`, model detail in `llm-eval.md`.

---

## 1. What the system eval proved about UCI

### 1.1 Structural extraction is near-perfect once a parser exists; semantics is the only gap
Current baseline (scoring 1.0): **mainframe 94.7 / 100, supported 98.6 / 100.** Every *structural*
and *honesty* category sits at or near 1.0 across all five datasets:

| category | carddemo | bank-of-z | cash-account | reading |
| --- | --- | --- | --- | --- |
| calls | 1.00 | — | — | call graph is exact |
| copybook_impact | 1.00 | 0.93 | 1.00 | the flagship "what breaks" question |
| jobs / transactions | 1.00 / 1.00 | — | — | job→program, tran→program |
| data_access / maps_to | 1.00 / — | 1.00 / 1.00 | 1.00 / 1.00 | SQL + DCLGEN lineage |
| completeness / gaps | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | the honesty contract |
| **queries** | **0.33** | **0.42** | **0.71** | **the one persistent weak cell** |

**The single durable finding:** deterministic extraction is a solved problem for the languages we
parse — the remaining weakness is *natural-language retrieval over terse names*, which is a
semantics problem, not a parsing one. `queries` did not move with better parsing or with FTS5; it
is the cell the LLM summaries pass exists to fix (§2.1). Everything structural is already exact.

### 1.2 The honesty categories never regressed — the guardrails hold under pressure
`completeness` and `gaps` stayed at 1.00 through the entire parser build-out **and** through the
addition of LLM-suggested edges. This is the most important stability result: every feature that
could have "cheated" its way to a higher structural score (dataflow resolution, LLM candidates)
was gated by the honesty categories, and none of them flipped a dynamic-dispatch site to `exact`
or polluted the gap report. The `llm-suggested`-outside-`RESOLVED_LEVELS` invariant is enforced by
the eval, not just asserted in a doc.

### 1.3 Mainframe estates are *more* statically analyzable than modern code
The determinism scoreboard from `uci metrics` on CardDemo: **480 `syntactic` call edges, 4
`inferred`, 25 honestly-unresolved dynamic sites.** ~94% of calls are provably resolved because
mainframe invocation is mostly literal (`CALL 'PGM'`, `EXEC PGM=`, CSD routing). This inverts the
usual assumption and is why the mainframe track reached 94.7 while `queries` — the one thing that
needs *understanding* rather than *parsing* — lags. The determinism story is strongest exactly
where the industry assumes it is weakest.

### 1.4 The eval earned its keep by catching real bugs — including dangerous ones
Building the eval *before* the parsers meant every change was measured. Bugs the eval caught that
code review did not:

- **Dataflow collapsed the menu router** — early `MOVE 'X' TO var` resolution turned CardDemo's
  `COMEN01C` one literal sign-off path into a false `exact`, hiding the table-driven dispatch.
  Caught by `completeness` dropping; fixed with taint tracking.
- **Subscripted XCTL silently vanished** — `PROGRAM(MENU-PGM(WS-IDX))` broke the regex and produced
  *neither* an edge *nor* an unresolved site, letting a dynamic program claim `exact`. The most
  dangerous class of bug (silent dishonesty), found by a completeness deduction.
- **Member-name collisions across artifact types** — `CBEXPORT` exists as both a program and the
  job that runs it; `CREACC.cbl` copies its own `CREACC.cpy`. Links attached to the wrong entity
  until resolution became path- and kind-aware.
- **Vacuity inflation** — external-only callers and empty-vs-empty gap checks were earning free
  1.0s, showing a misleading ~25/100 on an unindexed repo. The vacuity rules exist because the
  eval lied to us once.

### 1.5 A wrong golden is a finding too
`COSGN00C` was marked `expect_exact: false`; the tool said `exact` and was **right** (its XCTLs are
literal). The eval surfaced the disagreement, we read the source, and corrected the golden — with
the evidence recorded in `datasets/CHANGELOG.md`. The discipline that the miner never imports
`uci.*` is what makes "the tool disagrees with the golden" a real signal rather than a tautology.

---

## 2. What the LLM benchmark proved about models

### 2.1 Summaries are the measured retrieval win — and the reason the LLM layer exists
`queries` (§1.1) fails because a human asks "which program prints statements" and the answer is
`CBSTM03A` — nothing lexical matches. This is unfixable by parsing; it is exactly what one-line
LLM summaries (indexed into FTS + vectors) address. The benchmark confirms every tested model,
down to a 2B local one, writes usable summaries (0.65–1.00). This is the highest-ROI LLM
application because it targets the only sub-0.9 cell on the whole board.

### 2.2 Capability thresholds are real and sharp — restraint is the dividing line
The `candidates_restraint_when_opaque` task (abstain when the dispatch variable is caller-supplied)
splits models cleanly:

| model | one-shot restraint |
| --- | --- |
| qwen3.5:2b / :4b (local) | 0.00 |
| gemma4:e4b (local) | 0.00 |
| **gemini-2.5-flash** | **1.00** |

Every local model **hallucinates** call targets it cannot see; the frontier model, given the
hardened prompt, **abstains**. Restraint — knowing what you don't know — is the capability that
separates a safe enrichment model from an unsafe one, and it is invisible to accuracy-only
metrics. This is the benchmark's most valuable single number.

### 2.3 The cheap tasks don't need a big model
JSON discipline, DCLGEN field dictionaries, and answer-location routing (`ask`) score 1.00 on a 2B
local model. Deployments can run summaries/fields/ask on cheap local models and reserve a capable
model only for the judgment-heavy `candidates` pass — the benchmark tells you exactly where the
spend matters.

### 2.4 Thinking models have two specific failure modes the benchmark exposed
1. **Empty content** — qwen3.5 spent its entire token budget on the `thinking` field and returned
   nothing (`done_reason=length`). Fixed by `think:false` on Ollama + an informative error.
2. **Truncated summaries** — gemini-2.5-flash's server-side reasoning ate the 160-token summary
   budget, cutting the answer to "PRODINQ is a COBOL" (0.30). Fixed by raising the summary budget
   to 220 and documenting that thinking models want a higher `UCI_LLM_MAX_TOKENS`.

Both are provider/model-shape failures that never appear in a functional test with a fake client —
only a benchmark against real models finds them.

### 2.5 The benchmark argued *against* a feature we built — the highest form of eval value
The bounded agentic tool-loop was built to fix the restraint failure. The benchmark then showed:
- The **cheap** fix (a hardened one-shot prompt) already solves restraint on a capable model
  (gemini 1.00), removing the loop's motivation.
- The loop's *unique* job — cross-file resolution (pull the copybook holding the dispatch table) —
  is **unsolved by every model tested**, frontier included (all return `[]` after pulling; 0.10).

So the loop neither restores what the prompt fixed nor delivers what it was meant to add. It ships
**opt-in, off by default**, gated behind `agentic_cross_file_resolution ≥ 0.8` — a bar no model
has cleared. A well-built, plausible feature that the evidence says not to enable is precisely what
"measure before adopting" is for.

---

## 3. Methodological observations

1. **Eval-first changes the failure economics.** Because the mainframe track started at 0.0 by
   construction and rose only as parsers landed, every PR's value was a number, and every
   regression was caught the same run. The four review→fix cycles earlier in the project
   (`recommendations.md` §11–13) hit diminishing returns precisely because they were read-throughs;
   the eval is the instrument that read-throughs could not be.
2. **Independence is load-bearing.** The miner never imports `uci.*`; if it did, the eval would
   measure the tool agreeing with itself. This one rule is what makes §1.5 (a wrong golden) a
   signal instead of noise.
3. **Vacuity rules are not optional.** Empty-vs-empty comparisons inflate any young suite; the eval
   over-reported by ~3× until they were added. Any category that can score 1.0 by producing
   nothing needs a vacuity guard.
4. **Two evals, never merged.** The system eval (deterministic, CI-safe, guardrails applied) and
   the model eval (needs a provider, scores raw ability) answer different questions. Blending them
   would hide both the "structure is solved" story and the "restraint is the capability line"
   story. Track scores are likewise never blended (`supported` vs `mainframe`).
5. **Versioning caught the incomparability it was built for.** Five golden revisions in two days
   made early reports mutually incomparable; the `(version, fingerprint)` contract now refuses
   apples-to-oranges baseline comparisons and reports drift instead of a false regression.

---

## 4. Open questions the evals have surfaced (not yet answered)

- **`queries` after summaries** — the predicted 0.33 → ≥0.7 lift is not yet measured on the demo
  repos (needs an enriched run + an enriched baseline under the versioning rules). This is the next
  concrete experiment.
- **Cross-file resolution** — no model solves `agentic_cross_file_resolution`. Is it a prompt
  problem, a model-capability problem, or a tool-ergonomics problem? Unknown until a stronger model
  (gpt-4o / claude / a paid Gemini tier) runs the agentic tasks.
- **Scale envelope** — every score here is on repos ≤ ~130 files. The full-rebuild indexer and
  in-memory graph have no measured ceiling; a large-repo benchmark is still owed.
- **Capability golden for `capabilities`/`ask`** — these are validated for JSON honesty and target
  validity, but "did it group the right programs / name the right table" is spot-checked, not
  scored against a curated golden yet.
