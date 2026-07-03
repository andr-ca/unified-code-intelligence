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
| **qwen3-coder-480b** | freellm (frontier) | **91.4** | 0.88 | 1.00 | **1.00** | 1.00 | 1.00 | 0.60 |
| gpt-4.1 | freellm (frontier) | 90.5 | 0.88 | 1.00 | **1.00** | 1.00 | 1.00 | 0.55 |
| gemma4:e4b | local (~4B) | 80.0 | 0.88 | 1.00 | 0.50 | 1.00 | 1.00 | 0.42 |
| **gemini-2.5-flash-lite** | freellm (frontier-lite) | **77.5** | 0.65 | 1.00 | **0.50** | 1.00 | 1.00 | 0.50 |
| qwen3.5:2b | local (~2B) | 65.3 | 0.77 | 0.00¹ | 0.50 | 1.00 | 1.00 | n/a |
| qwen3.5:4b | local (~4B) | 62.8 | 0.77 | 0.00¹ | 0.50 | 1.00 | 1.00 | 0.50 |

The headline is the **interleaving**: `gemini-2.5-flash-lite`, a frontier model, scores **below**
local `gemma4:e4b` — because it fails the one task that matters most (restraint, §3). "Frontier"
is not a tier you can trust by label; it is a per-model, per-task property you must measure.

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

## 4. Finding 3 — tools do NOT earn their keep as a default

The bounded tool-loop (`--agentic`) exists to fix the restraint hole by letting the model *fetch*
the missing definition. The benchmark measures whether that works, now across **five** agentic-tested
models:

| model | tier | one-shot restraint | agentic restraint (tools) | agentic cross-file (tools-only) | cross-file calls |
| --- | --- | --- | --- | --- | --- |
| qwen3-coder-480b | freellm | 1.00 | 1.00 (no gain) | 0.20 | 3 → `[]` |
| gpt-4.1 | freellm | 1.00 | 0.90 (no gain) | 0.20 | 1 → `[]` |
| gemini-2.5-flash-lite | freellm | 0.00 | **0.90** ✅ recovered | 0.10 | 1 → `[]` |
| qwen3.5:4b | local | 0.00 | **0.90** ✅ recovered | 0.10 | 2 → `[]` |
| gemma4:e4b | local | 0.00 | 0.20 ❌ | 0.63 | **0** (never pulled) |

A cleaner rule than the first run suggested — three regimes, still none of which makes tools a
default:

1. **For restraint-*passing* models, tools are redundant.** `qwen3-coder-480b` and `gpt-4.1` are
   already 1.00 one-shot; the loop matches (1.00 / 0.90) at 1–3× the cost for no gain.
2. **For restraint-*failing* models that actually pull, tools RECOVER restraint** — a real,
   repeatable win: `gemini-2.5-flash-lite` and `qwen3.5:4b` both went **0.00 → 0.90** by fetching
   the `LINKAGE` definition and then abstaining. This is the one genuine value the loop delivers.
3. **But "actually pull" is itself unreliable on weak models.** `gemma4:e4b` made **zero tool
   calls** and hallucinated anyway (0.00 → 0.20). Same tools, opposite outcome, because whether a
   small model *chooses* to use them is not guaranteed.

And the decisive one, unchanged and now confirmed on five models:

4. **The loop's *unique* capability — cross-file resolution — is unsolved by everyone** (0.10–0.63,
   every model returning `[]` or noise). `qwen3-coder-480b` pulled the copybook holding the dispatch
   table **three times and still returned `[]`**. Fetching the evidence and *reasoning over it* are
   different skills; no model tested has the second. gemma's 0.63 is not a win — the log shows it
   guessed two real entries plus two noise programs (`PAYRUN`, `PRODINQ`), not a clean resolution.

**Conclusion:** `--agentic` stays **opt-in and off by default**. The recommended path is to **pick a
restraint-passing model** (§3) and skip the loop — then tools are pure overhead. The loop is only a
*remediation* worth considering when you are stuck with a restraint-failing model that reliably
pulls (and even then it does nothing for cross-file). No model clears the adoption bar
(`agentic_cross_file_resolution ≥ 0.8`, `docs/agentic-enrichment.md` §6); until one both fetches the
copybook *and* resolves it, the loop is a research feature.

## 5. Finding 4 — the call log turns "tools didn't help" into "here's why"

Scores say tools didn't help. The **per-call log** (`evals/reports/llm-logs/llm-eval-<run>.jsonl`)
says *why*, and that mechanism is the whole reason logging is on by default:

- **gemma's restraint failure is a non-use, not a mis-reason.** Its `agentic_restraint` row logs a
  single call — it answered immediately without pulling the `LINKAGE` definition. You cannot see
  that in a 0.20 score; you can see it in `1 call`.
- **The frontier's cross-file failure is a mis-act, not a non-fetch.** Its `agentic_cross_file` rows
  log 4 calls (3 `get_source`/`rag_search` pulls + 1 answer) — it fetched the table and *then*
  returned `[]`. That is the finding that keeps the loop a research feature: the bottleneck is
  reasoning-over-fetched-evidence, so more/better tools won't move it.
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
| Turn on `--agentic`? | **No** (opt-in only) | §4 — redundant on restraint-passers, unreliable on weak models, cross-file unsolved by all five |
| `UCI_LLM_MAX_TOKENS` for verbose/thinking models | raise from default (≥ 900 for `capabilities`) | §1 note ¹, §5 |
| Keep call logging on? | **Yes** (default) | §5 — the mechanism behind every verdict here came from the log |

**Before trusting any model for `candidates`, run `candidates_restraint_when_opaque` against it.**
That one task predicts whether the model will invent call edges it cannot see — and its answer does
not follow from the model's size, price, or brand (§3).

## 7. Reproduce

```bash
# free-frontier tier via the local gateway (key in .env; never committed)
python3 evals/llm_eval.py --protocol freellm \
    --models qwen3-coder-480b,gpt-4.1,gemini-2.5-flash-lite --agentic --timeout 150

# local tier
python3 evals/llm_eval.py --protocol ollama --models qwen3.5:4b,gemma4:e4b --agentic

# then mine the call log the runs wrote:
#   evals/reports/llm-logs/llm-eval-<run>.jsonl   (one JSON object per call, grouped by `tag`)
```

Both write a versioned JSON report to `evals/reports/` and a JSONL call log to
`evals/reports/llm-logs/`. Scores are comparable only within the same `tasks_version` (here 2).
