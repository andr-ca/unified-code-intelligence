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
| 1 | Schema — `DOC_SECTION` + `DESCRIBES` | ✅ | DOC_SECTION kind, DESCRIBES relation (not in DEPENDENCY_LIKE), specs+aliases; 15 tests |
| 2 | Detection — doc languages, `is_doc()`, config | ✅ | md/rst/adoc/txt/html/pdf/docx + README/CHANGELOG; index_docs/weight_doc/doc_max_bytes; scanner gates |
| 3 | Converter registry — PDF/DOCX → text | ✅ | docconvert.py lazy imports; indexer read path; `[docs]` extra; 2 pass/2 skip (libs not installed) |
| 4 | DocParser — structure (sections) | ✅ | heading→section, setext/adoc/html/page markers; registered per dialect; 3 tests |
| 5 | DocParser — mention extraction | ✅ | path/code-span/heading/bare + fence skip + stoplist; 7 tests |
| 6 | GraphBuilder — resolve `describes` (honesty ladder) | ✅ | doc-path/heading/code-span/mention ladder; documented-artifact gaps; impact-neutral; 3 tests; full suite 319 pass |
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
- **Tasks 1–6** — deterministic core landed: schema (DOC_SECTION/DESCRIBES), doc detection + config, PDF/DOCX converter, DocParser (structure + mentions), and the GraphBuilder honesty ladder (doc-path/heading/code-span/mention → DESCRIBES; documented-artifact gaps; impact-neutral). Full suite **319 passed**, 2 skipped (converter libs), 2 pre-existing eval failures. Each task committed on the branch.
