# Documentation Ingestion & Graph Linkage

**Status:** implemented · on by default (`UCI_INDEX_DOCS=1`)
**Plan:** [`document-ingestion-plan.md`](document-ingestion-plan.md) · **Status log:** [`document-ingestion-plan-status.md`](document-ingestion-plan-status.md)

UCI ingests repository **documentation** (Markdown, RST, AsciiDoc, plain text, HTML — plus PDF/DOCX
with an optional extra) into the canonical graph as first-class `DOC_SECTION` entities, and
deterministically links each doc section to the **code it describes** via a new `DESCRIBES`
relationship. Documentation then flows into hybrid retrieval, impact/symbol packs, the dashboard,
and the MCP tools — with an optional LLM linking pass and a scored precision/recall eval.

Documentation is treated like git metadata: **part of the codebase picture**, not a separate silo.

---

## 1. What gets ingested

| Format | Extensions / names | Language id | Requires |
| --- | --- | --- | --- |
| Markdown | `.md .markdown` | `markdown` | stdlib |
| reStructuredText | `.rst` | `rst` | stdlib |
| AsciiDoc | `.adoc .asciidoc` | `asciidoc` | stdlib |
| Plain text | `.txt`, and extensionless `README CHANGELOG CONTRIBUTING INSTALL NOTICE TODO` | `doctext` | stdlib |
| HTML | `.html .htm` | `htmldoc` | stdlib |
| PDF | `.pdf` | `pdf` | `pip install -e ".[docs]"` (`pypdf`) |
| Word | `.docx` | `docx` | `pip install -e ".[docs]"` (`python-docx`) |

`LICENSE` is intentionally **not** a doc (noise). PDF/DOCX are binary: they are detected by
extension, converted to text (DOCX headings → markdown `#` lines; PDF pages → `[uci-page N]`
markers the parser turns into per-page sections), and skipped silently when the converter library
isn't installed — never an error.

Each document's existing FILE/MODULE node **is** the document; its sections ride under the MODULE
exactly like a COBOL `PARAGRAPH` rides under a `LEGACY_PROGRAM`. There is no separate `DOCUMENT`
entity (avoids double-rooting; zero storage migration).

---

## 2. The mention-resolution ladder

The `DocParser` extracts **mention candidates** and classifies them; the `GraphBuilder` resolves
each through a confidence-labeled ladder that mirrors call resolution. A link is created only on a
**unique** match; ambiguous/multi-match mentions produce no edge.

| Rung | Evidence in the doc | `resolution` | confidence | Resolves to |
| --- | --- | --- | --- | --- |
| 1 | Path / markdown link to an indexed file (`app/cbl/COSGN00C.cbl`) | `doc-path` | 0.95 | FILE by path |
| 2 | Heading contains a resolvable name (`## COSGN00C — Signon`) | `doc-heading` | 0.95 | symbol inventory |
| 3 | Backtick code-span (`` `COSGN00C` ``, `` `pkg.Class.method` ``) | `doc-code-span` | 0.90 | qname, then name |
| 4 | Bare member-shaped token in prose/tables (`COSGN00C`, `CC00`) | `doc-mention` | 0.85 | name, unique only |

**Guardrails (all rungs):** unique match required (fan-out cap); stub (`missing`/`external`)
targets are never linked; a stoplist (`COBOL`, `CICS`, `AWS`, `README`, …) + system utilities +
`gap_external_prefixes` are excluded; `PARAGRAPH` targets are excluded in v1; fenced code blocks
(```` ``` ````) are treated as quoted code, not prose mentions. Every edge carries
`attributes.match`, `attributes.context` (the mention line, truncated), `resolution`, and
provenance with the mention's line number.

---

## 3. Configuration

| Env var | Config field | Default | Meaning |
| --- | --- | --- | --- |
| `UCI_INDEX_DOCS` | `index_docs` | `1` (on) | Master switch for the whole doc pipeline. `0` skips docs and re-ignores `*.pdf`/`*.docx`. |
| `UCI_WEIGHT_DOC` | `weight_doc` | `0.8` | Post-fusion multiplier on `DOC_SECTION` retrieval hits (docs enrich but never swamp code). `0` removes doc hits entirely. |
| `UCI_DOC_MAX_BYTES` | `doc_max_bytes` | `10000000` | Size cap for converter formats (PDF/DOCX) only. |

All keys ship (sanitized) in [`.env.sample`](../.env.sample).

---

## 4. Gaps — documented but missing

Only a **backticked/code-span, member-shaped** mention that resolves to nothing becomes a gap
record: `report_gap("documented-artifact", NAME, …, reason="documented-artifact-missing")`, and a
`DESCRIBES` edge to the stub with `resolution="missing"` (so the gaps panel and doc page can show
it, exactly like an unresolved `COPY` member). Bare-prose and heading/path misses are dropped as
noise — honest but low-noise.

---

## 5. Impact honesty — docs never inflate blast radius

`DESCRIBES` is **excluded** from `DEPENDENCY_LIKE` and `RESOLVED_LEVELS`. A README mentioning
`COSGN00C` must not grow its blast radius: doc links never change **risk**, **completeness**, or
callers/callees. They appear only in a dedicated `documentation` stratum of the impact pack and in
retrieval graph-expansion (a doc hit pulls its programs; a program hit pulls its docs).

---

## 6. Surfaces

### CLI
```bash
uci docs                 # documents · sections · links, coverage %, undocumented key artifacts
uci docs --json          # machine-readable docs_overview()
```

### MCP tools
- **`search_docs(query, top_k=10)`** — documentation-only search; `DOC_SECTION` hits with excerpts.
- **`get_documentation(symbol)`** — the doc sections describing a symbol (confidence-labeled links
  + excerpts): the design/spec context a safe change needs.

Both are gated by `available` (present iff the index has ≥1 `DOC_SECTION`), so agents never call an
always-empty tool. Full contracts: [`mcp-tools.md`](mcp-tools.md).

### Dashboard
`/docs` (in the **Understand** menu) — a coverage stat, the documents table (path · sections ·
links, each linking to a per-document detail), and the undocumented-key-artifacts table. The
per-document view lists sections in order with their `DESCRIBES` links (resolution badge +
confidence) and section text.

### Retrieval
Doc sections are chunked into FTS5 + vectors (secret-scrubbed like all chunks). A doc hit is
labeled **"Documentation describes this / matches the query"** and weighted by `UCI_WEIGHT_DOC`.

---

## 7. Optional LLM pass — `doc_links`

`uci enrich --pass doc_links` links **unlinked** doc sections (those the deterministic ladder
couldn't resolve — e.g. prose that describes a program without naming it). The model is shown the
section text + a bounded inventory of candidate names and asked which the section is *about*; every
returned name is **validated against the index** (hallucinations dropped) and written with
`extractor="llm:<model>"`, `resolution="llm-suggested"`, `confidence=0.6` — outside
`RESOLVED_LEVELS`, so it stays in the candidates stratum and never touches impact. Cached by section
content hash. Zero-LLM baseline works fully without it.

---

## 8. Evaluation

`evals/docs_eval.py` scores the deterministic linker against a hand-labeled golden
(`evals/datasets/doc_links/carddemo.json`): **recall** (expected section→artifact pairs found) and
**precision** (no forbidden false positives like `COBOL`/`AWS`). Gate: `precision ≥ 0.9 and
recall ≥ 0.8`. CardDemo currently scores **1.00 / 1.00**.

```bash
PYTHONPATH=src python3 evals/docs_eval.py           # scorecard + evals/reports/docs-eval-*.json
```

Tune only via the deterministic knobs (stoplist, member shape, kind-preference order) — never by
special-casing a golden.

---

## 9. Honesty notes

- Docs never affect impact/risk/completeness (`DESCRIBES ∉ DEPENDENCY_LIKE`).
- PDF/DOCX line numbers refer to the **extracted** text, not the original page layout; PDF sections
  are page-anchored via `[uci-page N]` markers.
- LLM `doc_links` edges are always `resolution="llm-suggested"`, confidence `0.6`, and never enter
  the resolved ladder.

## 10. Non-goals (v1)

Live connectors (Confluence/SharePoint), markdown→HTML rendering in the dashboard, `PARAGRAPH`-level
doc links, image/diagram OCR, doc-drift detection, and modern-code bare-prose (CamelCase without
backticks) linking. See the plan's "Non-goals" section.
