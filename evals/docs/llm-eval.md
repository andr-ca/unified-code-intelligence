# LLM-Eval — Model Capability Benchmark for Enrichment Tasks

**Date:** 2026-07-02 · **Tasks version:** 3 (v2 added agentic tasks; v3 added `architecture_overview`; bump on any task/golden change per `versioning.md`)
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
| `architecture` | layered system facts (API/Service/Data + edges, entry points, key symbols) | valid `{overview, key_points}` (0.4) + grounding: names the real layers (0.6) |

Scores: per-area mean → overall (0–100). Reports land in `evals/reports/llm-eval-*.json` with
per-task notes and latency. No repo indexing involved — pure prompt→response scoring, so a full
3-model run takes ~2 minutes locally.

## First scorecard (2026-07-02, local Ollama)

| model | overall | summaries | capabilities | candidates | fields | ask |
| --- | --- | --- | --- | --- | --- | --- |
| gemma4:e4b | 87.7 | 0.88 | 1.00 | 0.50 | 1.00 | 1.00 |
| qwen3.5:4b | 85.3 | 0.77 | 1.00 | 0.50 | 1.00 | 1.00 |
| qwen3.5:2b | 83.3 | 0.77 | 0.90 | 0.50 | 1.00 | 1.00 |

### Updated scorecard (2026-07-03, with improved harness + qwen3.6)

| model | overall | summaries | capabilities | candidates | fields | ask | notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **qwen3.6** | **95.3** | 0.77 | 1.00 | 1.00 | 1.00 | 1.00 | ⭐ new best local model |
| gemma4:e4b | 87.7 | 0.88 | 1.00 | 0.50 | 1.00 | 1.00 | baseline unchanged |
| qwen3.5:4b | 65.3 | 0.77 | 0.00 | 0.50 | 1.00 | 1.00 | ⚠️ JSON truncation regression |

Key change: qwen3.6 (23GB, 9B+ params) shows that restraint (1.00) is achievable on local models with larger capacity. Previous smaller models (4B, 2B) all scored 0.00 on restraint, confirming it's a capability threshold, not a training artifact.

**Findings from eval runs (2026-07-02 through 2026-07-03):**
1. **Thinking models can silently return empty content** — qwen3.5 burned its whole token budget
   on the `thinking` field (`done_reason=length`, empty message). Fixed in `LlmClient` (Ollama
   `think:false` + an informative error), found the first time the client met a real provider.
2. **Restraint is a capability threshold, not a size label** — Model restraint varies wildly by
   model, not by size/brand (shown in llm-comparison.md §3). qwen3.6 (9B+) scores 1.00; gemma4b
   and qwen3.5 (2–4B) score 0.00 across the board. Production guardrails contain this (unverifiable
   names are discarded; `llm-suggested` never resolves), but it reveals a clear capability gap
   that must be **measured per model before deployment**.
3. **JSON completion depends on model size and context** — qwen3.5:4b truncates JSON mid-response
   (capabilities=0.00), while qwen3.6 completes cleanly (1.00). The 4B model runs out of tokens
   even at 1200 max_tokens; larger models handle it easily. Token budget is not the bottleneck;
   the model's context window and generation efficiency are.
4. Even smaller local models are fully reliable on fields, ask routing, and formatted output —
   the cheap (non-judgment) tasks don't need large models; restraint-critical tasks do.

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
