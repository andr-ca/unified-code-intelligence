# LLM Enrichment — Optional Semantic Analysis Layer

**Date:** 2026-07-02
**Status:** implemented (`uci.enrich`); optional in every profile — the platform is fully functional without it.
**Origin:** `recommendations.md` §7(b) — "an LLM-enrichment adapter fits the provenance machinery"; the five passes below were selected in the analysis of where an LLM helps *understanding* most (summaries → retrieval being the only sub-0.9 eval cell).

---

## 1. Principles (non-negotiable)

1. **The LLM never creates resolved facts.** Everything it writes carries `extractor="llm:<model>"`
   and `confidence < 1.0`. LLM-proposed call edges use `resolution="llm-suggested"`, which is
   **excluded from `RESOLVED_LEVELS`** — they appear in `candidates` strata, never drive
   multi-hop traversal, and never flip `completeness` to `exact`.
2. **Enrichment is a separate, optional pass** (`uci enrich`), never part of `uci index` and never
   in the MCP query path. No provider configured → everything else works exactly as before.
3. **Deterministic facts are the input, not the output.** Prompts are grounded in parsed structure
   (calls, tables, jobs, transactions) + bounded source excerpts; outputs are validated against
   the index (e.g. a proposed call target must be an indexed member or it is discarded).
4. **Cached by content hash.** Each enriched entity records the source hash it was computed from;
   re-running `uci enrich` only pays for changed files. Budget-capped per run (`--limit`).
5. **Local-first stays local-first.** The default protocol is Ollama on localhost; cloud providers
   are opt-in via config. All protocols speak plain HTTP via the standard library — no SDK
   dependency.

## 2. Configuration

All via `Config` / environment / the repo's `.uci/.env` (see `.env.example`):

| Setting              | Env var                                  | Default                                                                                                                         | Notes                                                                                                                                                                                                                                         |
| -------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Protocol             | `UCI_LLM_PROTOCOL`                       | `ollama`                                                                                                                        | `ollama` (native API) · `openai` (OpenAI-compatible: OpenAI, vLLM, LM Studio, LiteLLM, gateways) · `anthropic` (Messages API) · `freellm` (local OpenAI-compatible gateway, default `localhost:3001`; empty model → the gateway auto-selects) |
| Base URL             | `UCI_LLM_URL`                            | per protocol: `http://localhost:11434` / `https://api.openai.com/v1` / `https://api.anthropic.com` / `http://localhost:3001/v1` | any OpenAI-compatible server works with `protocol=openai` or `freellm`                                                                                                                                                                        |
| API key              | `UCI_LLM_API_KEY`                        | *(empty — optional)*                                                                                                            | required by cloud providers and `freellm`; unused by local Ollama. Never written to reports or `Config.to_dict()`                                                                                                                             |
| Model                | `UCI_LLM_MODEL`                          | `qwen2.5-coder:7b` (ollama) / `gpt-4o-mini` (openai) / `claude-haiku-4-5-20251001` (anthropic) / *empty* (freellm)              | pick per protocol; leave empty so `freellm` auto-selects the best model                                                                                                                                                                       |
| Timeout / max tokens | `UCI_LLM_TIMEOUT` / `UCI_LLM_MAX_TOKENS` | `60` s / `700`                                                                                                                  | per request                                                                                                                                                                                                                                   |
| Call log             | `UCI_LLM_LOG`                            | *(empty → `<repo>/.uci/llm-calls.jsonl`)*                                                                                       | append every call as JSONL for offline analysis; `off`/`0`/`false` disables; a path redirects. See §2.1                                                                                                                                       |

`uci enrich --dry-run` shows what would be sent (counts + one sample prompt) without calling anything.

### 2.1 Call logging (for offline analysis)

Every completion is appended to a JSONL log **by default** (`.uci/llm-calls.jsonl`) — one line per
call: `ts, protocol, model, tag, max_tokens, latency_ms, ok, error, {system,user,response}` and
their char counts. The **API key is never logged** (records are built from call arguments, not the
client secret). Logging is **best-effort**: a write failure never breaks the LLM call. Disable with
`UCI_LLM_LOG=off`, or redirect with a path.

The `tag` attributes each call to a pass or eval task (`summaries`, `candidates:MROUTER`,
`qwen3-coder-480b:agentic_restraint`, …) via `LlmClient.default_tag`, so a log can be grouped by
capability, model, or run. This is what powers the with/without-tools analysis in
`evals/docs/llm-comparison.md` — e.g. counting that a model made **1** tool call on a restraint task
(didn't pull the evidence → hallucinated) vs **4** (pulled, then abstained). The LLM-eval writes a
per-run log to `evals/reports/llm-logs/llm-eval-<run>.jsonl`.

## 3. The five passes

### Pass 1 — `summaries` (retrieval semantics; the measured win)
- **For**: `LEGACY_PROGRAM`, `COPYBOOK`, `JCL_JOB`, `MODULE` (code files), `TRANSACTION_CODE`.
- **Prompt**: entity name/kind/language + structural facts (what it calls, what calls it, tables/
  datasets touched, jobs running it) + the first ~80 code/comment lines.
- **Output**: one–two sentence purpose summary → `attributes.summary` on the entity **plus a
  synthetic summary chunk** (`kind="summary"`, linked via `entity_id`) so the text enters both
  the FTS keyword signal and the embedding vectors.
- **Why**: NL queries fail on terse member names (`CBSTM03A` vs "which program prints account
  statements") — the `queries` eval category (0.28–0.33 on CardDemo) measures exactly this gap
  and will measure the fix.
- **Guardrail**: summaries are display/retrieval text only; they create no edges.

### Pass 2 — `capabilities` (business/domain mapping)
- **For**: the whole repo, after summaries exist.
- **Prompt**: the program inventory (names + summaries + transactions/jobs context), asking for
  strict JSON `[{name, description, programs[]}]` where `programs` may only use provided names.
- **Output**: `BUSINESS_CAPABILITY` entities + `IMPLEMENTS_CAPABILITY` edges (confidence 0.7).
  Unknown program names in the response are discarded (validated against the index).
- **Why**: turns 106 opaque members into "Payments / Statements / Card Authorization" — the
  "graph that teaches" view and the modernization engagement question ("show me everything that
  implements billing").

### Pass 3 — `briefing` (modernization report; on-demand, not an index pass)
- **Surface**: `uci briefing <symbol>` (and `Engine.briefing`). Renders the *already-proven*
  impact pack (callers/callees/tests/config/data/overrides/churn/risk/completeness/gaps) into a
  prose migration-readiness briefing.
- **Guardrail**: the prompt contains only graph facts with their file:line provenance and
  instructs the model to cite them; the JSON impact pack is returned alongside the prose so every
  claim is checkable. Nothing is written to the graph.

### Pass 4 — `candidates` (dynamic-dispatch proposals; the guardrailed one)
- **For**: unresolved `dynamic-target` call sites (CardDemo: 25).
- **Prompt**: the site's surrounding source (±40 lines, where the dispatch table/VALUES usually
  live) + the repo's program inventory; strict JSON `{"candidates": [...]}`, choose only from the
  inventory, empty when unsure.
- **Output**: `CALLS` edges with `resolution="llm-suggested"`, `confidence = min(0.5, 1/N)`;
  proposed names not in the index are **discarded** (never stubs, never gaps).
- **Guardrails** (enforced by the existing machinery): `llm-suggested ∉ RESOLVED_LEVELS` ⇒
  candidates stratum only, no multi-hop, `completeness` stays non-`exact`, and the `completeness`
  eval category must not move — that is the regression check for this pass.

### On-demand — `ask` (answer-location routing: code vs data vs not-in-repo)
- **Surface**: `uci ask "<question>"` (and `Engine.ask`). On-demand like `briefing`, not an index pass.
- **The problem it solves**: some questions are not answerable from code at all — *"what products
  does the app support?"* is defined by **rows in a table** (e.g. a product-catalog table), not by
  any program. A code-only tool answers those badly or not at all; the honest answer is *"that's
  data-resident: query `STOCKTRD.PRODUCT_CATALOG` (written by `PRODLOAD`, read by `INQPROD`;
  fields: PROD-ID, PROD-NAME …)"*.
- **How**: hybrid search results for the question + the repo's data inventory (tables/datasets with
  their reader/writer programs and pass-5 field dictionaries) go into the prompt; the model returns
  strict JSON `{"answer_location": "code"|"data"|"not_in_repo", "targets": [{name, kind, why}],
  "explanation", "next_step"}`.
- **Guardrails**: `targets` are validated against the index (unknown names are dropped and the
  answer degrades to `not_in_repo`); each target is returned with its *graph-proven* readers/
  writers attached, so the routing is checkable. `not_in_repo` answers connect to the gap-registry
  mindset: the tool names what artifact/data *would* be needed instead of hallucinating an answer.

### Pass 5 — `fields` (data dictionary)
- **For**: copybooks (DCLGEN first, then plain record layouts).
- **Prompt**: the copybook source; strict JSON `{"fields": [{name, meaning}]}`.
- **Output**: `attributes.data_dictionary` on the `COPYBOOK` entity (field → meaning), surfaced in
  the dashboard/detail views and available to briefings. No edges in v1 (column-level `MAPS_TO`
  can build on this later).

## 4. Where the LLM is deliberately NOT used

- Structural edges parsers prove deterministically (calls/imports/COPY/…) — the parsers are
  faster and verifiable; the determinism scoreboard stays clean.
- The MCP structured query path (latency + nondeterminism).
- NL→query-language generation (the code-graph-rag hallucination surface; fixed tools won).

## 5. Storage & provenance

- Entity attribute writes re-upsert the entity with `attributes.llm = {model, pass, source_hash}`.
- Capability entities/edges: `extractor="llm:<model>"`, confidence 0.7.
- Candidate edges: `extractor="llm:<model>"`, `resolution="llm-suggested"`, confidence ≤ 0.5.
- Cache: metadata state `enrich:<pass>` → `{entity_id: source_hash}`; `--force` ignores it.
- A full re-index rebuilds the graph and *drops* LLM facts (full-rebuild semantics); `uci enrich`
  re-applies from cache cheaply (only the LLM calls for changed sources are re-paid).

## 6. Evaluation contract

- **Pass 1 is gated by the `queries` category** — run the eval with enrichment applied to the demo
  repos and compare (expected: carddemo `queries` 0.33 → ≥0.7). Enriched runs are marked in the
  report (`enriched: true`) and compared only against enriched baselines (versioning rules apply).
- **Pass 4 must not move `completeness`** — the honesty categories are its regression gate.
- Passes 2/5 get curated goldens later (capability grouping spot-checks).
- CI never requires an LLM: enrichment tests use a deterministic fake client.
- **Model selection is benchmarked separately** by `evals/llm_eval.py` (`evals/docs/llm-eval.md`):
  the production prompts against golden fixtures, scored per task area — run it against candidate
  models/providers before changing `UCI_LLM_MODEL`.
