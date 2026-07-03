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

| model | tier | one-shot restraint |
| --- | --- | --- |
| qwen3.5:2b / :4b (local) | local | 0.00 |
| gemma4:e4b (local) | local | 0.00 |
| gemini-2.5-flash-**lite** | frontier-lite | **0.00** |
| **gemini-2.5-flash** | frontier | **1.00** |
| **gpt-4.1** | frontier | **1.00** |
| **qwen3-coder-480b** | frontier | **1.00** |

Restraint — knowing what you don't know — is the capability that separates a safe enrichment model
from an unsafe one, and it is invisible to accuracy-only metrics. The 2026-07-03 run (six models,
`llm-comparison.md`) added the sharper half of the finding: **restraint does not track the "frontier"
label.** `gemini-2.5-flash-lite` hallucinates like a 2B local, while the full `gemini-2.5-flash`,
`gpt-4.1`, and `qwen3-coder-480b` abstain — the capability flips *within one vendor's lineup* by
tier. So it must be **measured per model** (`candidates_restraint_when_opaque` is that gate), never
assumed from size or brand. This is the benchmark's most valuable single number.

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

### 2.5 The agentic tool-loop: how a "the models can't" result turned out to be "our harness wouldn't let them" — a case study

This is the richest methodological episode in the project. It is documented in full because the
mistake it contains — reading a tooling failure as a model-capability ceiling — is the single
easiest way to draw a wrong conclusion from an agentic benchmark.

**What we built and why.** Dynamic dispatch (`EXEC CICS XCTL PROGRAM(WS-DISPATCH)` where
`WS-DISPATCH` is loaded from a table) can't be resolved from the ±40-line window around the call —
the deciding information (the table's literal `VALUE 'ACCTVIEW'…` entries) lives in a *copybook*.
So we built a bounded tool-loop that lets the model *fetch* that context: read a source slice, ask
the graph for relationships, search by name — then answer. The eval task `agentic_cross_file_resolution`
puts the dispatch table in `cpy/DISPTBL.cpy`, which the seed window does not show; the loop must go
get it. `agentic_restraint` is the opposite: the variable is fed from `LINKAGE`, so the correct
answer is to abstain.

**What didn't work — and, crucially, WHY.** The first run scored 0.10–0.63 across five models, every
one returning `[]` or noise. We wrote it up as "cross-file resolution is unsolved by every model,
frontier included; the loop is a research feature." **That conclusion was wrong.** Reading the
per-call log (default-on JSONL, §2.4's sibling win) showed the copybook's contents *never entered
any model's context* — three distinct harness defects, each provable from a transcript:

1. **`search` was blind to the thing the models correctly asked for.** `search` was `find_by_name`
   over graph *entities*, and a COBOL data item like `MENU-PGM` is not its own node — it lives
   inside a copybook. gpt-4.1 reasoned correctly (`search MENU-PGM`), got **"no matches"**, and —
   given no evidence — correctly abstained (`[]`). A perfect chain of reasoning defeated by a blind
   tool.
2. **`get_source` gave no end-of-file signal, so models burned their budget re-reading.**
   `qwen3-coder-480b` asked for `MROUTER.cbl` lines 1–100, then 1–200, then 1–300 — the file is 10
   lines. Each call returned the same 10 lines with no "this is the whole file," so the model kept
   asking, spent its entire budget, and never reached the copybook.
3. **The discovery tools were never wired in.** `rag_search` (hybrid RAG) and `list_files` — the two
   tools that would have located `cpy/DISPTBL.cpy` in one call — existed for production `ask` but
   were never passed into the candidates loop. And nothing resolved a `COPY DISPTBL` statement to
   its path, so models that tried guessed wrong (`cbl/DISPTBL.cpy`).

The models were reasoning correctly on evidence they were structurally prevented from obtaining.

**How we diagnosed it.** Entirely from the call log. The scores said "0.20, returned `[]`" for every
model — indistinguishable from a genuine reasoning failure. The *transcript* said "searched the
right symbol → tool returned no-match" and "re-read a 10-line file three times." Without the log we
would have shipped the wrong conclusion. (This is why LLM logging is on by default — see the
recommendation in `llm-comparison.md` §5/§6.)

**How we fixed it — four changes, each aimed at one defect, no model change.**
1. `search` now **falls back to keyword/RAG** when there is no graph node, so `search MENU-PGM`
   returns the *file* that contains it (`cpy/DISPTBL.cpy`) instead of a dead "no matches."
2. `get_source` now **resolves `COPY MEMBER` → its indexed path** and prints total length +
   `END OF FILE`. Reading `MROUTER.cbl` now directly yields `COPY DISPTBL → cpy/DISPTBL.cpy`, and
   the model never re-reads a file it has already seen whole.
3. `rag_search` + `list_files` **wired into the loop** (budget 3 → 4).
4. The prompt was **rebalanced**: read THIS program's own source first; only open a copybook the
   program actually COPYs; abstain on `LINKAGE`/`USING`/`COMMAREA`; never borrow another program's
   table.

**What started working — and why.** `qwen3-coder-480b` went **agentic 0.60 → 1.00** (cross-file
0.20 → **1.00**, restraint **1.00**), overall **91.4 → 98.0** — the first model ever to clear the
`agentic_cross_file_resolution ≥ 0.8` adoption gate. The *why* is legible in the transcript: it now
reads `MROUTER.cbl` (fix 2 hands it `→ cpy/DISPTBL.cpy` inline), opens that copybook, reads the two
literal `VALUE` entries, and answers exactly `['ACCTVIEW','ACCTEDIT']` in **two** tool calls — while
on the `LINKAGE` case it sees no COPY, correctly abstains (fix 4), in one call. Fixes 1 and 3 give
alternative discovery paths for models that search the data item rather than the copybook.

**What the intermediate attempts taught us (precision vs recall).** A first prompt revision that only
pushed "go find the copybook table" drove gpt-4.1's cross-file to 0.90 but **broke its restraint to
0.10** — primed to expect a table, it borrowed `DISPTBL`'s values on the `LINKAGE` case where no
table exists. The rebalanced prompt (fix 4: verify the table belongs to *this* program; abstain on
`LINKAGE`) restored restraint. Lesson: the discovery prompt is a **precision/recall dial**, and it
is **model-specific** — `qwen3-coder-480b` was robust to it; gpt-4.1 was not.

**What still doesn't fully work / honest caveats.** gpt-4.1 is prompt-sensitive and **provider-flaky**
— its final cross-file run returned empty completions (a 44 s freellm stall) *after* navigating
correctly to the copybook, so its 0.10 there is transport noise, not reasoning. All of this is on a
tiny synthetic fixture at n=1 clean; a larger, real copybook-dispatch repo is still owed. The fix is
also now wired into production (`_pass_candidates`), so the benchmark represents what ships.

**The lasting methodological lesson.** *A negative agentic result is guilty until the call log proves
the evidence reached the model.* "The model can't" and "our harness never let it" produce **identical
scores**; only the transcript distinguishes them. Harness quality dominated model quality here — the
same models swung 0.20 → 1.00 on tooling alone. The loop is **viable**; it stays opt-in for
cost/variance/per-model-prompt-tuning reasons, not because it fails. Full breakdown in
`llm-comparison.md` §4; design + correction in `../../docs/agentic-enrichment.md` §6.

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
