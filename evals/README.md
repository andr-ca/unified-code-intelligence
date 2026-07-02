# evals/ — UCI Evaluation Suite

Measures UCI's promises (exact structure, honest completeness, gap awareness) against real
repositories. **Read `docs/evaluation.md` (design) and `docs/scoring.md` (the exact scoring
contract) first.**

```
evals/
  docs/            evaluation design + scoring contract (start here)
  demo-repos/      real mainframe repos (CardDemo, Bank-of-Z, cash-account, CBSA*)
  fixtures/        small Python fixtures (shop, resolve_cases)
  datasets/        golden datasets (*.json) + mined/ reference facts
  tools/           mine_ground_truth.py — independent reference extractor (never imports uci.*)
  run_eval.py      runner + scorer (implements docs/scoring.md)
  reports/         run reports + baseline.json (the regression gate)
```

Datasets and scoring are **versioned + fingerprinted** (docs/versioning.md, datasets/CHANGELOG.md):
the baseline gate only compares apples-to-apples; drifted datasets show informational deltas
until re-baselined.

Quick start:

```bash
PYTHONPATH=src python3 evals/run_eval.py                                   # full run
PYTHONPATH=src python3 evals/run_eval.py --dataset carddemo -v             # one dataset, verbose
PYTHONPATH=src python3 evals/run_eval.py --baseline evals/reports/baseline.json   # CI gate
```

Two tracks, never blended: **supported** (Python/JS — regression gate, currently ~94/100) and
**mainframe** (COBOL/JCL/CICS/DB2 — Phase 5 progress meter, currently 0 by construction: the
extractors don't exist yet; every one that lands should move exactly its categories).

*CBSA is a placeholder clone (README only) — excluded from datasets until source is present.*

First defect found by this suite (kept as a live example of what it's for): same-module
constructor calls (`DiscountRule()`) are recorded as `not-found` unresolved sites, making
completeness over-hedge (`shop` completeness = 0.5 instead of 1.0).
