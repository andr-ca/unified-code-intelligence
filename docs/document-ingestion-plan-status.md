# Documentation Ingestion — Implementation Status

**Plan:** [`document-ingestion-plan.md`](document-ingestion-plan.md)
**Branch:** `feat/doc-ingestion`
**Started:** 2026-07-03
**Baseline:** 283 tests passing (+ 2 pre-existing unrelated `test_eval.py` WIP failures)

Legend: ✅ done · 🚧 in progress · ⬜ not started

---

## Progress

| Task | Title | Status | Notes |
| --- | --- | --- | --- |
| 0 | Branch + baseline snapshot | ✅ | Branch `feat/doc-ingestion` created; baseline 283 pass |
| 1 | Schema — `DOC_SECTION` + `DESCRIBES` | ⬜ | |
| 2 | Detection — doc languages, `is_doc()`, config | ⬜ | |
| 3 | Converter registry — PDF/DOCX → text | ⬜ | |
| 4 | DocParser — structure (sections) | ⬜ | |
| 5 | DocParser — mention extraction | ⬜ | |
| 6 | GraphBuilder — resolve `describes` (honesty ladder) | ⬜ | |
| 7 | Indexer + chunking — doc sections into FTS/vectors | ⬜ | |
| 8 | Retrieval — doc weighting + `DESCRIBES` expansion | ⬜ | |
| 9 | Impact & symbol packs — `documentation` stratum | ⬜ | |
| 10 | Engine facade + MCP tools | ⬜ | |
| 11 | CLI — `uci docs` | ⬜ | |
| 12 | Dashboard — `/docs` page + API + nav | ⬜ | |
| 13 | Optional LLM pass — `doc_links` | ⬜ | |
| 14 | Eval — doc-linkage track | ⬜ | |
| 15 | Documentation updates | ⬜ | |
| 16 | Final verification gate | ⬜ | |

---

## Running log

- **Task 0** — feature branch `feat/doc-ingestion` created off `main`. Baseline test run: 283 passed, 2 pre-existing `test_eval.py` failures (unrelated WIP). Proceeding TDD task-by-task.
