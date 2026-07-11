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
| 7 | Indexer + chunking — doc sections into FTS/vectors | ✅ | DOC_SECTION chunkable; doc read/chunk gates; doc_sections/doc_links counters; 16 tests |
| 8 | Retrieval — doc weighting + `DESCRIBES` expansion | ✅ | doc reason+priority, weight_doc multiplier, DESCRIBES graph expansion; 9 tests |
| 9 | Impact & symbol packs — `documentation` stratum | ✅ | risk-neutral documentation stratum in impact + entity_detail; 10 tests |
| 10 | Engine facade + MCP tools | ✅ | search_docs/get_documentation/docs_overview + capabilities gating; 11 tests |
| 11 | CLI — `uci docs` | ✅ | documents/links/coverage/undocumented; 8 tests |
| 12 | Dashboard — `/docs` page + API + nav | ✅ | docs_page + doc_detail_page, /docs + /api/docs + /api/doc, Understand nav; 8 tests |
| 13 | Optional LLM pass — `doc_links` | ✅ | validated llm-suggested edges (conf 0.6), impact-neutral; 23 tests |
| 14 | Eval — doc-linkage track | ✅ | docs_eval.py precision/recall gate; carddemo golden 1.00/1.00 PASS; smoke + gate tests |
| 15 | Documentation updates | ✅ | documentation-ingestion.md guide + README/schema/mcp/dashboard/retrieval/enrichment/roadmap |
| 16 | Final verification gate | 🚧 | full suite + evals + smoke |

---

## Running log

- **Task 0** — feature branch `feat/doc-ingestion` created off `main`. Baseline test run: 283 passed, 2 pre-existing `test_eval.py` failures (unrelated WIP). Proceeding TDD task-by-task.
- **Tasks 1–6** — deterministic core landed: schema (DOC_SECTION/DESCRIBES), doc detection + config, PDF/DOCX converter, DocParser (structure + mentions), and the GraphBuilder honesty ladder (doc-path/heading/code-span/mention → DESCRIBES; documented-artifact gaps; impact-neutral). Full suite **319 passed**, 2 skipped (converter libs), 2 pre-existing eval failures. Each task committed on the branch.
