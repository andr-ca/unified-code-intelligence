# UCI Examples

These examples demonstrate the seven core capabilities. The first five run against the bundled
sample repository at [`tests/fixtures/sample_repo`](../tests/fixtures/sample_repo); the last two are
self-contained scripts that build canonical graphs directly (Phase-4/5 previews).

> Setup (from the `unified-code-intelligence/` directory):
> ```bash
> pip install -e .            # or: export PYTHONPATH=src
> uci index tests/fixtures/sample_repo
> ```
> All commands below accept `--path tests/fixtures/sample_repo` (or run them from inside that repo).

## 1. Semantic + hybrid search
Find code by meaning or by identifier — embeddings are just one signal.
```bash
uci query "where is the pricing calculated?" --path tests/fixtures/sample_repo
uci query "DiscountRule" --path tests/fixtures/sample_repo
```
Each hit shows the **signals** that fired (symbol/keyword/semantic/graph/proximity) and **why** it
was included.

## 2. Call-graph traversal
Exact structural answers from the graph, not guesses.
```bash
uci graph symbol PricingCalculator.calculate --path tests/fixtures/sample_repo
```
Shows callers (`place_order`, `test_calculate`) and callees (`DiscountRule.apply`).

## 3. Impact analysis — "what breaks if I change X?"
```bash
uci impact PricingCalculator.calculate --path tests/fixtures/sample_repo
```
Returns a structured pack: callers, callees, covering tests, config, churn, and a **risk score**.

## 4. Test discovery
```bash
uci impact PricingCalculator.calculate --json --path tests/fixtures/sample_repo | jq '.tests'
# or via MCP:  find_tests_for_symbol { "symbol": "PricingCalculator.calculate" }
```
Finds `test_calculate` via TESTS edges, test call-sites, and name matching.

## 5. Config-dependency discovery
```bash
uci impact pricing.calculator --json --path tests/fixtures/sample_repo | jq '.config'
```
Surfaces `MAX_DISCOUNT` (referenced in `calculator.py`, defined in `config.env`).

## 6. Data-lineage (mock, Phase-4 preview)
```bash
python examples/data_lineage.py
```
Builds a `functions → READS/WRITES → table`, `query → column`, `DTO → MAPS_TO → table` graph and
answers "who touches the `orders` table?" — the relationships Phase-4 SQL extractors will populate.

## 7. Legacy modernization (mock, Phase-5 preview)
```bash
python examples/legacy_modernization.py
```
Extracts the COBOL/JCL/copybook files in [`examples/legacy/`](./legacy) into the **same** canonical
schema (`LEGACY_PROGRAM`, `COPYBOOK`, `JCL_JOB`, `PARAGRAPH`, `RUNS`, `MAPS_TO`,
`CANDIDATE_FOR_MIGRATION`) with file:line provenance, then reports copybook usage, the JCL→program
run edge, field→column mappings, and migration candidates.

## Explore visually
```bash
uci serve --path tests/fixtures/sample_repo   # http://127.0.0.1:8765
```
Overview · module list · symbol search · graph explorer · impact view · architecture · onboarding.

## Use from a coding agent (MCP)
```bash
uci mcp --repo tests/fixtures/sample_repo
```
Exposes `search_code`, `find_symbol`, `get_callers`, `get_callees`, `impact_analysis`,
`explain_module`, `retrieve_edit_context`, `find_tests_for_symbol`, `find_data_lineage`,
`find_config_dependencies` — all returning structured JSON.
