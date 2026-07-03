# LLM-Eval — Model Capability Benchmark for Enrichment Tasks

**Date:** 2026-07-02 · **Tasks version:** 1 (bump on any task/golden change; `versioning.md` rules)
**Runner:** `evals/llm_eval.py` · **Separate from the main eval by design:** `run_eval.py` scores
the *system* (guardrails applied, deterministic, CI-safe); LLM-eval scores a *model's raw ability*
on the production prompts, so you can choose a model per deployment and catch provider-specific
failure modes before they reach enrichment.

```bash
python3 evals/llm_eval.py --list                        # model menu, groups, scopes
python3 evals/llm_eval.py --models local                 # local Ollama group, one-shot
python3 evals/llm_eval.py --models frontier --tools      # freellm group, with the tool-loop
python3 evals/llm_eval.py --models frontier --scope smoke # fast subset for a quick check
python3 evals/llm_eval.py --models qwen-coder,gemma4b     # mix tiers in one run
```

**Pick models** by alias (`qwen-coder`), group (`local` | `frontier` | `all`), raw `protocol:model`
(`freellm:gpt-4.1`), or bare name (on `--protocol`, default ollama). **Size the run** with
`--scope smoke|full`. **Toggle tools** with `--tools` / `--no-tools`. The menu (MODELS / GROUPS /
SCOPES) lives at the top of `llm_eval.py`. `openai`/`anthropic` still work via
`--protocol` + `UCI_LLM_API_KEY` and a bare model name.

## Task areas (production prompts, golden fixtures, deterministic scoring)

| Area | Tasks | Scored on |
| --- | --- | --- |
| `summaries` | COBOL inquiry program; CICS router | expected-keyword hits (0.7) + brevity ≤400 chars (0.3) |
| `capabilities` | 6-program inventory (payments/statements/products) | JSON validity, program-name honesty (no hallucinated members), coverage, sane group count |
| `candidates` | dispatch table with literal VALUES (golden {PGMA, PGMB}); **opaque COMMAREA variable (golden: abstain)** | F1 vs golden; the abstention task is all-or-nothing |
| `fields` | DCLGEN copybook | field coverage with non-trivial meanings |
| `ask` | data-resident question (products → PRODUCT_CATALOG); code-resident question (→ PRODFMT) | routing accuracy (0.6) + correct target (0.4) |

Scores: per-area mean → overall (0–100). Reports land in `evals/reports/llm-eval-*.json` with
per-task notes and latency. No repo indexing involved — pure prompt→response scoring, so a full
3-model run takes ~2 minutes locally.

## First scorecard (2026-07-02, local Ollama)

| model | overall | summaries | capabilities | candidates | fields | ask |
| --- | --- | --- | --- | --- | --- | --- |
| gemma4:e4b | 87.7 | 0.88 | 1.00 | 0.50 | 1.00 | 1.00 |
| qwen3.5:4b | 85.3 | 0.77 | 1.00 | 0.50 | 1.00 | 1.00 |
| qwen3.5:2b | 83.3 | 0.77 | 0.90 | 0.50 | 1.00 | 1.00 |

**Findings already produced by this eval:**
1. **Thinking models can silently return empty content** — qwen3.5 burned its whole token budget
   on the `thinking` field (`done_reason=length`, empty message). Fixed in `LlmClient` (Ollama
   `think:false` + an informative error), found the first time the client met a real provider.
2. **Every tested model fails the restraint task** (`candidates_restraint_when_opaque` = 0.00
   across the board): shown a dynamic call whose variable comes from an opaque COMMAREA field,
   all three invent candidates instead of abstaining. Production guardrails contain this
   (unverifiable names are discarded; `llm-suggested` never resolves), but it is the clearest
   model-capability gap — and the benchmark for prompt-hardening or agentic-context experiments.
3. Even a 2B local model is fully reliable on JSON discipline, DCLGEN dictionaries, and
   answer-location routing — the cheap tasks don't need a big model.

## Frontier model — gemini-2.5-flash (2026-07-02, Gemini OpenAI-compatible endpoint)

Run against Google's free tier (`--protocol openai --url .../v1beta/openai`, key in
`UCI_LLM_API_KEY`). Two findings that change the enrichment story:

1. **gemini-2.5-flash is the first model to PASS the one-shot restraint task (1.00)** — every
   local model scored 0.00. The hardened `_SYS_CANDIDATES` prompt (naming LINKAGE/COMMAREA) works
   *when the model is capable enough to follow it*. **This shrinks the tool-loop's value**: the
   loop was motivated by the restraint failure, and the cheap prompt fix already solves it on a
   frontier model. The loop's remaining justification is cross-file resolution alone — which no
   model yet does reliably (all return `[]` after pulling; 0.10).
2. **Thinking models truncate summaries at the small production budget** (summaries 0.30):
   gemini-2.5-flash spends part of the 160-token summary budget on server-side reasoning, cutting
   the answer to "PRODINQ is a COBOL". Production `_pass_summaries` raised its budget to 220 and
   the docs now note that thinking models want a higher `UCI_LLM_MAX_TOKENS`.

Operational note: the free tier heavily rate-limits (some models show `limit:0`; individual tasks
429 mid-run). The client now retries 429/500/502/503/529 with `Retry-After`/backoff, which cleared
the transient 503s — but sustained free-tier quota still flakes long runs. Use a paid key or run
tasks individually for a clean frontier scorecard.

## Agentic tasks (v2, `--agentic`)

`--agentic` adds the bounded tool-loop tasks (docs/agentic-enrichment.md): a dispatch table in a
copybook the seed window can't see (`agentic_cross_file_resolution`) and a LINKAGE-fed variable
that must be abstained on (`agentic_restraint`). First run (local Ollama): qwen3.5:4b restrains
well (0.90) but under-resolves cross-file (0.10); gemma4:e4b resolves cross-file (0.73) but
doesn't restrain (0.20). **Neither clears the adoption bar** — the loop stays opt-in. This is the
result the benchmark exists to surface before a plausible-but-unproven feature becomes a default.
