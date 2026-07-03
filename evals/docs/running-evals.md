# Running Evals: Quick Start

The LLM eval harness (`evals/llm_eval.py`) scores models against production prompts and golden fixtures. This guide shows all the ways to run it — from a quick one-liner to an interactive step-by-step mode.

## Quickest start: one-liner commands

All of these work immediately:

```bash
# See the menu
python3 evals/llm_eval.py --list

# Run a group of models, one-shot (no tools)
python3 evals/llm_eval.py --models local          # gemma4b + qwen4b
python3 evals/llm_eval.py --models frontier       # qwen-coder + gpt-4.1
python3 evals/llm_eval.py --models all            # all four

# Run with the tool-loop
python3 evals/llm_eval.py --models frontier --tools

# Quick sanity check: smoke scope, one-shot
python3 evals/llm_eval.py --models local --scope smoke

# Mix models from different tiers
python3 evals/llm_eval.py --models qwen-coder,gemma4b --tools

# Raw protocol:model syntax (for models not in the menu)
python3 evals/llm_eval.py --models freellm:gpt-4o,ollama:qwen3.5:4b
```

Output: report JSON in `evals/reports/`, call log in `evals/reports/llm-logs/` (one JSON line per LLM call, grouped by task).

## All flags explained

```
--models       What to run. Accepts:
                 • aliases: qwen-coder, gpt-4.1, gemma4b, gemini-lite, qwen4b, qwen2b
                 • groups: local (gemma4b+qwen4b), frontier (qwen-coder+gpt-4.1), all
                 • raw protocol:model: freellm:gpt-4.1, ollama:qwen3.5:4b
                 • bare names: uses --protocol (default ollama)
               Comma-separated, e.g. --models qwen-coder,gemma4b

--scope        Task set size. Options:
                 • smoke (fast, high-signal: 1 summary + restraint + 1 routing + agentic if --tools)
                 • full (all 8 one-shot + 2 agentic if --tools; default)

--tools        Enable the agentic tool-loop tasks (cross-file resolution, restraint).
--no-tools     One-shot only (default).
               Example: --models frontier --tools

--protocol     Default protocol for bare model names. Options:
                 • ollama (default; local keyless)
                 • freellm (localhost gateway; key in .env)
                 • openai, anthropic (need UCI_LLM_API_KEY env var)

--timeout      HTTP timeout in seconds (default 120). Raise for slow providers.

--list         Print the model/scope menu and exit. No run.

--agentic      Legacy alias for --tools (for muscle memory).
```

## The model menu (edit to add models)

Edit the tables at the top of `evals/llm_eval.py` to customize:

```python
MODELS: dict[str, ModelSpec] = {
    "qwen4b":      ModelSpec("qwen4b", "ollama", "qwen3.5:4b"),
    # ... your models here
}

GROUPS: dict[str, list[str]] = {
    "local":    ["qwen4b", "gemma4b"],
    "frontier": ["qwen-coder", "gpt-4.1"],
    # ... your groups here
}

SCOPES: dict[str, set[str] | None] = {
    "smoke": {"summary_prodinq", ...},  # fast subset
    "full": None,  # every task
}
```

## Interactive step-by-step mode

Don't like typing flags? Use the interactive CLI:

```bash
python3 evals/llm_eval.py --interactive
```

This walks you through:
1. **Pick models** — select from the menu or enter custom names
2. **Pick scope** — smoke (fast) or full
3. **Toggle tools** — with or without the agentic tool-loop
4. **Review** — confirm your choices
5. **Run** — execute and stream results

No flags required. Answer the prompts and go.

## Common workflows

### Sanity check (5–10 min)
```bash
python3 evals/llm_eval.py --models local --scope smoke
```
Runs 3 one-shot tasks on local models. No tools, fast feedback on whether the harness is alive.

### Quick frontier benchmark (15–20 min)
```bash
python3 evals/llm_eval.py --models frontier --scope smoke --tools
```
Same 3 one-shot + 2 agentic tasks, but on the free frontier tier. See if tools help.

### Full benchmark, one model (10–15 min per model)
```bash
python3 evals/llm_eval.py --models qwen-coder
```
All 8 one-shot tasks, one frontier model. No tools. Baseline performance.

### Full benchmark with tools, mixed tiers (20–30 min)
```bash
python3 evals/llm_eval.py --models qwen-coder,gemma4b --tools
```
All 8 one-shot + 2 agentic tasks. Compare a frontier model and a local model side-by-side. The report table shows both with their `protocol` column so you can easily see tier differences.

### Add a new model to the benchmark
1. Edit `MODELS` in `evals/llm_eval.py`:
   ```python
   "my-model": ModelSpec("my-model", "ollama", "my-model:latest"),
   ```
2. Run: `python3 evals/llm_eval.py --models my-model`

### Explore a single task in isolation
Edit `SCOPES["debug"]` to test just one task:
```python
SCOPES: dict[str, set[str] | None] = {
    # ...
    "debug": {"summary_prodinq"},  # just one task for fast iteration
}
```
Then: `python3 evals/llm_eval.py --models my-model --scope debug`

## Output and artifacts

**Report JSON** (e.g. `evals/reports/llm-eval-20260703T110624Z.json`):
```json
{
  "run": "2026-07-03T11:06:24Z",
  "tasks_version": 2,
  "scope": "smoke",
  "tools": true,
  "models": [
    {
      "model": "qwen-coder",
      "protocol": "freellm",
      "overall": 98.0,
      "areas": {"summaries": 0.88, "candidates": 1.00, "agentic": 1.00},
      "tasks": [
        {"task": "summary_prodinq", "score": 1.0, "seconds": 3.9, ...}
      ],
      "total_seconds": 42.7
    }
  ]
}
```

**Call log JSONL** (e.g. `evals/reports/llm-logs/llm-eval-20260703T110612Z.jsonl`):
```jsonl
{"ts":"2026-07-03T11:06:12.123Z","protocol":"freellm","model":"qwen3-coder-480b","tag":"qwen-coder:summary_prodinq","max_tokens":220,"latency_ms":3942,"ok":true,"system_chars":486,"user_chars":215,"response_chars":89,"system":"...","user":"...","response":"..."}
```

Each line is one LLM call, with full prompt/response and latency. Group by `tag` to see what each task cost. The key never appears (see `llm-enrichment.md` §2.1).

## Comparing runs

Use the report JSON timestamps and the `scope`/`tools` fields to find and compare runs:

```bash
# List all reports
ls -lh evals/reports/llm-eval-*.json

# Parse a specific report
python3 -c "import json; r = json.load(open('evals/reports/llm-eval-20260703T110624Z.json')); print(f\"{r['models'][0]['model']}: {r['models'][0]['overall']}\") "

# Compare two runs side-by-side (manually)
# - open two reports in your editor
# - look for the same model by name
# - compare `overall` and per-area scores
# - check `scope` and `tools` to verify you're comparing apples-to-apples
```

Runs with different `version` + `scope` + `tools` are not directly comparable (versioning rules in `evals/docs/versioning.md` §1).

## Troubleshooting

**Model not found / "no models resolved"**
- Check: `python3 evals/llm_eval.py --list`
- Add it to `MODELS` in `evals/llm_eval.py`
- Or use raw form: `--models freellm:my-model`

**Freellm gateway not reachable**
- Check: key in `.env` and gateway running at `localhost:3001`
- Fallback: use `--models local` (Ollama)

**LLM timeouts**
- Raise `--timeout` (default 120s)
- Or choose a faster model (e.g. `gemini-lite` instead of `gpt-4.1`)

**Call log not written**
- Check: `evals/reports/llm-logs/` exists and is writable
- LLM logging is default-on; disable with `--env UCI_LLM_LOG=off` if needed (see `llm-enrichment.md` §2.1)

## Under the hood

- **Fixtures** are frozen in `evals/llm_eval.py` (`_PRODINQ_SRC`, `_ROUTER_SRC`, etc.)
- **Scoring** is deterministic (same prompt + model → same score)
- **No repo indexing** — the eval is pure prompt→response, no UCI graph needed
- **Call logging** is automatic unless disabled; one JSON line per LLM request

For design and methodology, see:
- `evals/docs/evaluation.md` — the scoring contract
- `evals/docs/llm-eval.md` — task areas + evaluation approach
- `evals/docs/observations.md` — what the benchmarks taught us
- `evals/docs/llm-comparison.md` — the full local vs frontier + tools analysis
