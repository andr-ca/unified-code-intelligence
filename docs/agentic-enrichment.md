# Agentic Enrichment — Bounded Tool-Loop for Context-Starved LLM Tasks

**Date:** 2026-07-02
**Companion to:** `llm-enrichment.md` (the one-shot enrichment layer this extends) and
`evals/docs/llm-eval.md` (the benchmark that motivates and gates it).
**Status:** design + implementation for the `candidates` pass; other passes stay one-shot until
the benchmark justifies otherwise.

---

## 1. Motivation: measured, not assumed

LLM-eval run 2026-07-02 (three local models) produced one universal failure:

| task | gemma4:e4b | qwen3.5:4b | qwen3.5:2b |
| --- | --- | --- | --- |
| `candidates_from_value_table` (dispatch table with literal VALUES in view) | 1.00 | 1.00 | 1.00 |
| `candidates_restraint_when_opaque` (variable fed from an opaque COMMAREA field) | **0.00** | **0.00** | **0.00** |

Every model **hallucinated candidates** when the deciding information was not in the ±40-line
window. This is not a model-quality problem — it is a *context* problem: the model cannot know
whether `WS-DISPATCH` is table-driven or caller-supplied without seeing where its value comes
from, and that definition frequently lives in a **copybook or another section of the file**,
outside any fixed window we choose.

Two candidate fixes, both benchmarked before adoption:

1. **Prompt hardening** (cheap): instruct the model to abstain when the variable's value arrives
   from `LINKAGE SECTION` / `DFHCOMMAREA` / an external caller.
2. **Bounded tool-loop** (this document): let the model *request* the missing context — the
   variable's definition, a copybook body, the relationships of an entity — through the same
   verified surfaces the engine already exposes, then answer.

They compose: the hardened prompt is the base; the tool-loop supplies evidence the hardened
prompt can act on.

## 2. Design principles

1. **The loop is bounded, or it doesn't exist.** Hard caps enforced by the harness, not the
   prompt: **max 3 tool calls**, **max ~120 lines per source pull**, **max 2 search results per
   query**, one final answer. Exceeding a bound forces the answer turn. There is no "let the
   model explore" mode.
2. **Tools are the engine's own read-only surfaces.** The model can pull nothing the graph/repo
   doesn't already prove: a source slice, an entity's relationships, a name search over the
   index. No shell, no writes, no network.
3. **Same guardrails as one-shot.** The final answer is validated identically: proposed targets
   must exist in the index; accepted edges are `resolution="llm-suggested"`, confidence ≤ 0.5,
   outside `RESOLVED_LEVELS`; `completeness` cannot flip to `exact`. The loop changes what the
   model *sees*, never what it may *assert*.
4. **Caching must survive the loop.** The cache key is the hash of the initial context **plus
   every tool response actually served** (the "evidence transcript"). Unchanged evidence ⇒ cache
   hit ⇒ zero LLM calls on re-run — the same economics as one-shot enrichment.
5. **Determinism budget.** Temperature 0 throughout; the transcript (tool calls + responses) is
   recorded in the edge attributes (`attributes.evidence`) so a reviewer can replay exactly what
   the model saw when it proposed a candidate.
6. **Opt-in.** `uci enrich --pass candidates --agentic` (or `UCI_LLM_AGENTIC=1`). One-shot remains
   the default until LLM-eval shows the loop is strictly better for the configured model.

## 3. The protocol (works on all three wire protocols)

Native tool-calling APIs differ across ollama/openai/anthropic; a JSON-action protocol over plain
chat completions works identically on all three and keeps the client SDK-free. Each model turn
must be a single JSON object:

```jsonc
// tool request turns (at most 3 total):
{"action": "get_source",        "path": "cpy/DISPTBL.cpy", "start": 1, "end": 60}
{"action": "get_relationships", "name": "ROUTER"}
{"action": "search",            "query": "MENU-PGM"}

// final turn (mandatory; forced when budget is exhausted):
{"action": "answer", "candidates": ["PGMA", "PGMB"]}    // or [] to abstain
```

Harness behavior per turn:

| Event | Response |
| --- | --- |
| Valid tool request within budget | Tool result appended to the conversation as the next user message (`TOOL RESULT (n/3): …`) |
| `get_source` beyond 120 lines | Slice truncated to 120 lines, noted in the result |
| Unknown action / malformed JSON | One retry nudge (`Reply with a single valid JSON action`), then forced-answer turn |
| Budget exhausted | `TOOL BUDGET EXHAUSTED — you must answer now` message; only `answer` accepted |
| `answer` | Loop ends; payload validated by the caller exactly like a one-shot response |

Tool implementations (`enrich/tool_loop.py`):

- `get_source(path, start, end)` — repo-relative read, clamped to the repo root and 120 lines.
- `get_relationships(name)` — resolve via the graph, return in/out edges (type, other-end name,
  resolution label) capped at 12 each.
- `search(query)` — `find_by_name` (exact, then substring) over the index, top 2, with kind+path.
- `rag_search(query)` *(when a retriever is attached)* — the full hybrid RAG signal
  (symbol + FTS keyword + embeddings + graph expansion), top 5 hits with kind, path, reason, and
  any LLM summary. This is the "ask a follow-up question of the index" tool.
- `list_files(prefix?)` *(when a metadata store is attached)* — the indexed file inventory,
  optionally filtered by path prefix, capped at 30 entries with language tags.

## 3.1 Agentic `ask` — RAG follow-ups for answer-location routing

`uci ask "<question>" --agentic` runs the same answer-location router inside the tool-loop
(budget 4 instead of 3 — routing questions legitimately need one exploratory `rag_search` plus a
verification pull). The seed context is unchanged (top-8 hybrid hits + the data inventory); the
loop lets the model **re-query the RAG with reformulations** ("product catalog table"), **list
files** in a suspected area, and **read a specific file** before committing to
`code | data | not_in_repo`. All guardrails are identical: the final `targets` are validated
against the index, unverifiable answers degrade to `not_in_repo`, graph-proven readers/writers are
attached to data targets, and the response reports the tool transcript (`evidence`) so the routing
is auditable. Default remains one-shot; `--agentic` is per-invocation opt-in.

## 4. What this pass looks like end-to-end

For each unresolved `dynamic-target` site (unchanged worklist):

1. **Seed context** — identical to one-shot: ±40 lines around the site + program inventory +
   the hardened system prompt, now with the tool protocol appended.
2. **Loop** — typical successful trace on the CardDemo menu pattern:
   `get_source` of the copybook holding the dispatch table → sees literal `VALUE 'COACTVWC'…`
   entries → `answer {"candidates": ["COACTVWC", …]}`. Typical restraint trace:
   `get_source` of the LINKAGE section → sees the variable is caller-supplied →
   `answer {"candidates": []}`.
3. **Validation & write** — identical to one-shot (§2.3), plus `attributes.evidence` recording
   the tool transcript digest.

Cost envelope: worst case 4 completions per site (3 pulls + answer) vs 1 for one-shot. With
25 dynamic sites on CardDemo and caching, that is bounded and predictable; the `--limit` cap
applies to sites exactly as before.

## 5. Failure modes and their handling

| Failure | Handling |
| --- | --- |
| Model never answers (loops on tools) | Hard cap forces the answer turn; empty/invalid final ⇒ site stays unresolved (status quo — never worse than before) |
| Model requests files outside the repo | Path resolution is clamped to the repo root; attempt returns an error string as the tool result |
| Model hallucinates despite evidence | Unchanged: unverifiable names discarded; `llm-suggested` never resolves |
| Tool results blow the context | Slices capped; relationships capped; transcript capped (~6k chars) with oldest-first truncation |
| Provider without reliable JSON | The malformed-JSON retry nudge, then abstention — measured per model by LLM-eval before anyone enables `--agentic` |

## 6. Evaluation plan (gates adoption)

LLM-eval grows a `candidates_agentic` area (tasks version bump — one-shot task scores are not
comparable across the `_SYS_CANDIDATES` hardening change; `versioning.md` rules apply):

1. **`agentic_cross_file_resolution`** — the dispatch table lives in a *copybook*, not the seed
   window. One-shot **cannot** solve this (no amount of prompt engineering shows it the file);
   the loop must `get_source` the copybook and return exactly the table's programs. This is the
   task that justifies the loop's existence.
2. **`agentic_restraint`** — the variable is fed from `LINKAGE SECTION`; correct behavior is to
   pull the definition, see it is caller-supplied, and abstain. Directly attacks the 0.00 cell.
3. **`oneshot_restraint_hardened`** — the same opaque case against the hardened one-shot prompt,
   so the cheap fix and the loop are compared head-to-head per model.
4. Bounds compliance is scored, not assumed: exceeding the tool budget or emitting malformed
   actions costs points (`protocol_discipline` component).

**Adoption rule:** `--agentic` becomes the recommended default for a model only when its agentic
candidates area ≥ one-shot hardened area **and** protocol discipline ≥ 0.9, per the LLM-eval
report for that model. The main eval's `completeness` category remains the system-level
regression gate (agentic proposals must never flip it).

### First agentic scorecard (2026-07-02, local Ollama) — the loop is NOT yet a default

| model | one-shot restraint | agentic_cross_file | agentic_restraint |
| --- | --- | --- | --- |
| qwen3.5:4b (local) | 0.00 | 0.10 (used 3 calls, still returned `[]`) | **0.90** (pulled LINKAGE, abstained) |
| gemma4:e4b (local) | 0.00 | 0.73 (found the table + 2 noise names) | 0.20 (didn't pull, hallucinated) |
| **gemini-2.5-flash** | **1.00** | 0.10 (pulled 3×, still `[]`) | 0.90 (abstained) |

**The honest finding this benchmark was built to produce — and it argues *against* shipping the
loop as a default:**

- Small local models do **one** of the two behaviors well, never both (qwen cautious, gemma eager).
- **The frontier model changes the calculus entirely.** gemini-2.5-flash solves the *one-shot*
  restraint task (1.00) that motivated the loop — the cheap hardened prompt is enough when the
  model can follow it. Yet its *agentic* cross-file resolution is still 0.10 (pulls the copybook
  three times, then returns `[]`). So the loop neither restores what the prompt already fixed nor
  solves the one thing it was supposed to add.

**Conclusion:** `--agentic` stays **opt-in and off by default**, and the recommended path for a
capable model is the **hardened one-shot prompt**, not the loop. The loop's only unique value —
cross-file resolution — is unproven on every model tested; until a model clears
`agentic_cross_file_resolution ≥ 0.8` it is a research feature, not a default. This is exactly the
outcome "measure before adopting" exists to catch: a plausible, well-built feature that the
evidence says not to turn on. The harness and gate remain ready for the next model.

### Reproduced on a second frontier model (2026-07-03, freellm `qwen3-coder-480b`)

| model | one-shot restraint | agentic_cross_file | agentic_restraint | tool calls on cross_file |
| --- | --- | --- | --- | --- |
| **qwen3-coder-480b** (freellm) | **1.00** | 0.20 | 1.00 | 3 → returned `[]` |
| gemma4:e4b (local) | 0.00 | 0.63 (table + 2 noise) | 0.20 | **0** (never pulled) |
| qwen3.5:4b (local) | 0.00 | 0.10 | 0.90 (pulled `LINKAGE`) | 2 → returned `[]` |

A 480B code-specialist reproduces the exact pattern: it **solves one-shot restraint** (1.00, so the
loop's motivating task needs no loop) and **fails cross-file** (pulls the copybook 3× then returns
`[]`, 0.20). The bottleneck is confirmed as *reasoning over fetched evidence*, not fetching. The
call log (docs/llm-enrichment.md §2.1) also exposes that gemma's agentic restraint failure is a
**non-use** (0 tool calls), not a mis-reason — the full analysis is in
`evals/docs/llm-comparison.md`.

## 7. Explicit non-goals

- No agentic mode for `summaries`/`capabilities`/`fields` — the scorecard shows no context
  starvation there (0.77–1.0); adding loops would only add cost and variance.
- No multi-site planning ("investigate all 25 sites at once") — one site, one bounded loop.
- No write-capable tools, ever, under any budget.
- Not a general agent framework: three fixed tools, one fixed protocol, one pass.
