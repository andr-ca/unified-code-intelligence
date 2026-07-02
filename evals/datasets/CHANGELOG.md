# Eval Dataset Changelog

Every golden change bumps the affected dataset's `version` and gets an entry here
(policy: `evals/docs/versioning.md` §2). Newest first.

## v2 — 2026-07-02 (all datasets) · scoring 1.0

The parser-sprint revisions, adopted together as v2 for all five datasets:

- **jobs**: mined `procs` lists added; PROC invocations are neutral in F1 (a `RUNS` edge to a
  PROC member is correct but unmodeled by the program golden).
- **maps_to**: new category; `dclgens` mined from `EXEC SQL DECLARE <table> TABLE` in `.cpy`
  members (cash-account: 2, bank-of-z: 4).
- **programs**: `.asm`/`.hlasm` members counted as programs — reclassified CardDemo
  `COBDATFT`/`MVSWAIT` call targets from neutral to internal; Bank-of-Z program set 40 → 52
  (IMS PSBs are assembler).
- **carddemo/completeness**: `COSGN00C` corrected `expect_exact: false → true` — golden was
  wrong; its XCTLs are literal (`PROGRAM('COADM01C'/'COMEN01C')`, cbl ~231/236). Documented
  the anonymous-dynamic-dispatch caveat in `scoring.md` §6.5.
- **bank-of-z**: `BANKDATA` evidence path corrected (`src/base/cics/cobol/`, not batch).
- **data_access**: scoped to `database_table`-kind hits (dataset/VSAM answers are correct but
  unmodeled by the SQL-mined golden).
- **runner/scoring**: vacuity rules (external-only callers excluded from mined derivation;
  empty-vs-empty gaps excluded from aggregates); copybook self-name exclusion switched to
  path-based (`CREACC.cbl COPY CREACC.` is a real dependent).

Baseline after v2 + the full parser build-out: mainframe 94.7, supported 98.6.

## v1 — 2026-07-01 · initial

- `shop`, `resolve_cases`: migrated to the unified schema from the original team datasets
  (golden content unchanged).
- `carddemo`, `cash-account`, `bank-of-z`: created — mined facts (calls, copybooks, jobs,
  transactions, data_access) + curated queries/completeness/gaps.
- Baseline at v1: mainframe 0.0 (no mainframe parsers existed), supported 93.6.
