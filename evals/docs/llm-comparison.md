# LLM Comparison — Local vs Free-Frontier, With vs Without Tools

**Date:** 2026-07-03
**Harness:** `evals/llm_eval.py` (tasks_version 2), temperature 0, production prompts against golden
fixtures. Every call logged to `evals/reports/llm-logs/` (docs/llm-enrichment.md §2.1).
**Companion to:** `llm-eval.md` (task definitions), `observations.md` (standing synthesis),
`../../docs/agentic-enrichment.md` (the tool-loop this benchmark gates).

This document answers two procurement questions with numbers, not intuition:

1. **Local small model vs a free frontier model** — where does the spend actually buy accuracy?
2. **With tools vs without tools** — does letting the model fetch missing context (the bounded
   tool-loop) beat a single hardened prompt?

The "free frontier" tier is served by **freellm**, a localhost-only OpenAI-compatible gateway that
fronts large models (here `qwen3-coder-480b`). It never leaves the machine, so it is a legitimate
"free" tier for benchmarking without exposing the codebase.

---

## 1. The scoreboard (run 2026-07-03, tasks_version 2)

Three free-frontier models (via the local freellm gateway) against three local Ollama models:

| model | tier | overall | summaries | capabilities | candidates | fields | ask | agentic |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **qwen3-coder-480b** | freellm (frontier) | **98.0**² | 0.88 | 1.00 | **1.00** | 1.00 | 1.00 | **1.00**² |
| gpt-4.1 | freellm (frontier) | 90.5 | 0.88 | 1.00 | **1.00** | 1.00 | 1.00 | 0.50–0.55² |
| gemma4:e4b | local (~4B) | 80.0 | 0.88 | 1.00 | 0.50 | 1.00 | 1.00 | 0.42 |
| **gemini-2.5-flash-lite** | freellm (frontier-lite) | **77.5** | 0.65 | 1.00 | **0.50** | 1.00 | 1.00 | 0.50 |
| qwen3.5:2b | local (~2B) | 65.3 | 0.77 | 0.00¹ | 0.50 | 1.00 | 1.00 | n/a |
| qwen3.5:4b | local (~4B) | 62.8 | 0.77 | 0.00¹ | 0.50 | 1.00 | 1.00 | 0.50 |

Two headlines. First, the **interleaving**: `gemini-2.5-flash-lite`, a frontier model, scores
**below** local `gemma4:e4b` — because it fails the one task that matters most (restraint, §3).
"Frontier" is not a tier you can trust by label; it is a per-model, per-task property you must
measure. Second, the **agentic column moved once the harness was fixed** (§4): `qwen3-coder-480b`
went 0.60 → 1.00 on tooling alone.

² `qwen3-coder-480b` with the **fixed agentic harness** (§4b). With the original blind harness it
scored agentic 0.60 / overall 91.4. gpt-4.1's agentic swings with prompt version and a provider
flake (§4b note); its non-agentic 90.5 is stable.

¹ `capabilities` 0.00 for the qwen3.5 locals is a **truncation** failure, not a reasoning failure:
the model emitted valid-looking JSON that ran past the token budget (`'[{"name": "PAYRUN", "des…`)
and failed to parse. It is a `UCI_LLM_MAX_TOKENS` tuning issue, consistent with `observations.md`
§2.4 (thinking/verbose models need more headroom), not evidence the model can't group programs.

## 2. Finding 1 — the frontier premium is ONE pass (`candidates`), and only for strong models

The two strong frontier models (`qwen3-coder-480b` 91.4, `gpt-4.1` 90.5) beat the best local
(`gemma4:e4b` 80.0) by ~10–11 points, and that entire gap is **one task area: `candidates`**
(0.50 → 1.00, the judgment-heavy dynamic-dispatch pass — see Finding 2). Everywhere else the
strong-frontier and local rows are identical.

The cheap tasks split into two groups:

- **`fields` and `ask` tie at 1.00 across every tier** — down to a 2B local model. Nothing about a
  bigger model improves field dictionaries or answer-location routing on these fixtures.
- **`summaries` does *not* uniformly favour the frontier.** `gemini-2.5-flash-lite` scored **0.65**
  — *below* the local models' 0.77–0.88 — because its terser output missed scored keywords. A
  frontier-lite model can be a *worse* summariser than a 2B local one.

**Deployment consequence (sharpened):** run `fields` / `ask` (and, with a decent local model,
`summaries`) locally; spend the frontier budget on `candidates` **and only on a frontier model that
passes restraint** (§3). This both reproduces and tightens `observations.md` §2.3 — the spend is
justified in exactly one pass, and only for a subset of "frontier" models.

## 3. Finding 2 — restraint is the dividing line, and it does NOT track the "frontier" label

`candidates` decomposes into two tasks. Every model resolves a **visible** dispatch table
(`candidates_from_value_table`, all 1.00). They split on **restraint**
(`candidates_restraint_when_opaque`: abstain when the dispatch variable is fed from an opaque
`LINKAGE`/`COMMAREA` field the window can't see):

| model | tier | one-shot restraint |
| --- | --- | --- |
| **qwen3-coder-480b** | freellm frontier | **1.00 (abstained)** |
| **gpt-4.1** | freellm frontier | **1.00 (abstained)** |
| gemini-2.5-flash-lite | freellm **frontier-lite** | **0.00 (hallucinated `PGMA/PGMB/PGMC`)** |
| gemma4:e4b | local | 0.00 (hallucinated + `OTHER`) |
| qwen3.5:2b / :4b | local | 0.00 (hallucinated) |

Restraint is a real **capability threshold** — but the surprise from adding a third frontier model
is that **it does not track model size or the "frontier" label.** Two strong frontier models
(`qwen3-coder-480b`, `gpt-4.1`) abstain; a *cheaper* frontier model (`gemini-2.5-flash-lite`)
hallucinates exactly like the 2–4B locals. An earlier full `gemini-2.5-flash` (non-lite) passed at
1.00 (`observations.md` §2.2) — so the capability can be present or absent **within a single vendor's
lineup**, purely by tier.

**The operational consequence is strong:** you cannot infer restraint from price, size, or brand.
It must be **measured per model** with this exact task before that model is trusted for the
`candidates` pass. `candidates_restraint_when_opaque` is that gate; a model scoring 0.00 there will
invent call edges it cannot see, and no amount of "it's a frontier model" reasoning changes that.

## 4. Finding 3 — the tool-loop works; the first "it doesn't" result was a BLIND HARNESS

> **Correction (2026-07-03).** An earlier version of this section concluded "cross-file resolution
> is unsolved by every model; the loop is a research feature." **That was wrong** — it measured a
> deficient harness, not the models. When the harness was fixed, `qwen3-coder-480b` went from
> agentic **0.60 → 1.00** (cross-file 0.20 → **1.00**, restraint **1.00**) — overall 91.4 → **98.0**
> — with **no model change**. This is the cleanest example in the whole suite of a benchmark
> measuring its own instrument. The original data is kept below as the "blind" baseline.

### 4a. The blind baseline (tools present, discovery tools absent)

First agentic run, with only `get_source` / `get_relationships` / `search` (graph-only):

| model | one-shot restraint | agentic restraint | agentic cross-file | what the call log showed |
| --- | --- | --- | --- | --- |
| qwen3-coder-480b | 1.00 | 1.00 | **0.20** | re-read the 10-line program 3× (no EOF signal), never reached the copybook in budget |
| gpt-4.1 | 1.00 | 0.90 | **0.20** | `search MENU-PGM` → "no matches" (data items aren't graph nodes) → correctly abstained on no evidence |
| gemini-2.5-flash-lite / qwen3.5:4b | 0.00 | 0.90 | 0.10 | pulled `LINKAGE`, abstained (restraint recovered) but never located the table |
| gemma4:e4b | 0.00 | 0.20 | 0.63 | made **0** tool calls; guessed the table + 2 noise names |

The log (§5) proved the copybook contents **never entered any model's context** — `search` was
blind to the data item the models correctly asked for, `get_source` gave no end-of-file signal so
models re-read the same slice, and the discovery tools (`rag_search`, `list_files`) that production
`ask` has were **never wired into the candidates loop**. The models were reasoning correctly on
evidence they were never allowed to obtain.

### 4b. The fixed harness (four targeted changes, no model change)

1. **`search` RAG-fallback** — a name with no graph node (e.g. the data item `MENU-PGM`) now falls
   back to keyword/RAG and returns the *file* that contains it (the copybook). No more dead ends.
2. **`get_source` resolves `COPY MEMBER` → path** and prints total length + `END OF FILE`. Reading
   the program now directly yields `COPY DISPTBL → cpy/DISPTBL.cpy`; models stop guessing paths and
   stop re-reading.
3. **`rag_search` + `list_files` wired into the loop** (they already existed for `ask`).
4. **Prompt rebalanced** — "read THIS program's own source first; only open a copybook it actually
   COPYs; abstain on `LINKAGE`/`USING`/`COMMAREA`; never borrow another program's table."

Result on the two strong frontier models:

| model | agentic cross-file | agentic restraint | agentic area | overall |
| --- | --- | --- | --- | --- |
| **qwen3-coder-480b** | 0.20 → **1.00** (2 calls, exact) | 1.00 | 0.60 → **1.00** | 91.4 → **98.0** |
| gpt-4.1 | 0.20 → 0.90 → **0.10*** | 0.10 → 0.90 | 0.55 → 0.50 | ~90 |

`*` gpt-4.1 is **prompt-sensitive and provider-flaky**: an intermediate prompt drove cross-file to
0.90 but broke restraint to 0.10; the balanced prompt fixed restraint but its final cross-file run
returned **empty completions** (a 44 s freellm stall) *after* navigating perfectly to the copybook —
a transport flake, not a reasoning miss. Its swing is noise; `qwen3-coder-480b` ran clean at 1.00.

### 4c. The corrected conclusion

- **The loop is viable.** A capable model, given an adequate harness, resolves cross-file dispatch
  **and** abstains on opaque input — `qwen3-coder-480b` scores 1.00 on both, reproducibly, in ≤2
  tool calls. "No model can do this" was never true; it was untested.
- **Harness quality dominates model quality here.** The same models swung 0.20 → 1.00 on tooling
  alone. Before concluding a model "can't," verify from the call log that the evidence actually
  reached it.
- **Prompt design is a real precision/recall balance and is model-specific** — the version that
  maximised gpt-4.1's recall broke its restraint. It must be co-tuned and re-benchmarked per model.
- **`--agentic` stays opt-in by default** — but now for a *cost/variance* reason (extra calls,
  provider flakiness, per-model prompt tuning), **not** because "it doesn't work." For a strong,
  stable model on copybook-dispatch estates it is now a defensible *on* choice. The adoption gate
  (`agentic_cross_file_resolution ≥ 0.8`) is **cleared by `qwen3-coder-480b` (1.00)** — the first
  model to do so.

## 5. Finding 4 — the call log turns "tools didn't help" into "here's why"

Scores say tools didn't help. The **per-call log** (`evals/reports/llm-logs/llm-eval-<run>.jsonl`)
says *why*, and that mechanism is the whole reason logging is on by default:

- **gemma's restraint failure is a non-use, not a mis-reason.** Its `agentic_restraint` row logs a
  single call — it answered immediately without pulling the `LINKAGE` definition. You cannot see
  that in a 0.20 score; you can see it in `1 call`.
- **The frontier's cross-file failure was a non-fetch, not a mis-reason — and the log is what
  proved it.** The 0.20 score looked like "fetched the table, then failed to reason." The transcript
  said otherwise: `qwen3-coder-480b` re-read the 10-line *program* three times (no EOF signal) and
  never opened the copybook; gpt-4.1 searched the data item and got "no matches." The evidence never
  arrived. This is the bullet that overturned §4's original conclusion — once the harness delivered
  the copybook, the same model went 0.20 → **1.00**. Better tools *did* move it.
- **Latency and shape** are captured too: freellm frontier calls averaged ~1.7 s; the thinking
  models on the gateway (`gpt-oss-120b`, `deepseek-v4-flash`) leak server-side reasoning into
  `content` and need a higher `UCI_LLM_MAX_TOKENS` — a deployment note only a real-call log surfaces.

Each record carries `{ts, protocol, model, tag, max_tokens, latency_ms, ok, error, system, user,
response}` and never the API key. `tag` (`<model>:<task>`) is what makes the grouping above a
one-liner over the JSONL.

---

## 6. Recommendations (evidence-backed)

| decision | recommendation | evidence |
| --- | --- | --- |
| Model for `fields`/`ask` | cheap **local** model | §2 — all tiers tie at 1.00 |
| Model for `summaries` | a **decent local** model (0.77–0.88); avoid frontier-*lite* (0.65) | §2 — `gemini-2.5-flash-lite` underperforms locals here |
| Model for `candidates` | a **restraint-passing** model — `qwen3-coder-480b` or `gpt-4.1`; **not** `gemini-2.5-flash-lite` | §3 — restraint is per-model, must be measured, ignores the "frontier" label |
| Turn on `--agentic`? | **Opt-in**, but now *defensible on* for a strong, stable model on copybook-dispatch estates | §4 — a fixed harness took `qwen3-coder-480b` to 1.00 on both agentic tasks; the earlier "off" was a harness artifact |
| Before trusting the agentic loop | verify from the **call log** that fetched evidence reached the model | §4/§5 — the first negative result was a blind harness, not the models |
| `UCI_LLM_MAX_TOKENS` for verbose/thinking models | raise from default (≥ 900 for `capabilities`) | §1 note ¹, §5 |
| Keep call logging on? | **Yes** (default) | §5 — the mechanism behind every verdict here came from the log |

**Before trusting any model for `candidates`, run `candidates_restraint_when_opaque` against it.**
That one task predicts whether the model will invent call edges it cannot see — and its answer does
not follow from the model's size, price, or brand (§3).

## 7. Reproduce

```bash
python3 evals/llm_eval.py --list                      # see the model menu / scopes

# free-frontier tier (key in .env; never committed), with tools:
python3 evals/llm_eval.py --models frontier --tools --timeout 150

# local tier, with tools:
python3 evals/llm_eval.py --models local --tools

# mix tiers, or size the run down for a quick check:
python3 evals/llm_eval.py --models qwen-coder,gemma4b --tools     # one frontier + one local
python3 evals/llm_eval.py --models frontier --scope smoke --tools # fast subset

# then mine the call log the runs wrote:
#   evals/reports/llm-logs/llm-eval-<run>.jsonl   (one JSON object per call, grouped by `tag`)
```

Both write a versioned JSON report to `evals/reports/` and a JSONL call log to
`evals/reports/llm-logs/`. Scores are comparable only within the same `tasks_version` (here 2).
