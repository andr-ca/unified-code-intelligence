# Documentation Ingestion & Graph Linkage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Follow superpowers:test-driven-development within each task and superpowers:verification-before-completion before claiming any task done.

> **Status (2026-07-11):** Plan reviewed and approved. Tasks 0–5 are implemented and committed on `feat/doc-ingestion` (`7ad7ff4`…`5b8f068`), all their tests green (43 passed, 2 optional-dep skips). **Resume at Task 6.** Known pre-existing failures unrelated to this feature: `tests/test_eval.py::test_eval_meets_thresholds` and `::test_eval_per_resolution_level_is_precise` (`KeyError: 'callgraph'`) fail identically at pre-doc commit `9e246a2` — do not attempt to fix them inside this feature branch.

**Goal:** Ingest repository documentation (Markdown/RST/AsciiDoc/plain text/HTML, plus PDF/DOCX via an optional extra) into the canonical graph as first-class `DOC_SECTION` entities, deterministically link doc sections to the code entities they describe via a new `DESCRIBES` relationship, and surface documentation in hybrid retrieval, impact/symbol packs, the dashboard, and MCP tools — with an optional LLM linking pass and a scored eval.

**Architecture:** Documentation becomes another language family in the existing pipeline (scan → parse → normalize → graph → chunk → embed). A registered `DocParser` emits sections as `ParsedSymbol(kind=DOC_SECTION)` and mentions as `ParsedLink(relation="describes")`; the `GraphBuilder` resolves mentions through a confidence-labeled ladder mirroring call resolution, feeding the existing gap registry for documented-but-missing artifacts. `DESCRIBES` is deliberately **excluded** from `DEPENDENCY_LIKE` (docs must never inflate impact analysis) but **included** in retrieval graph-expansion (a doc hit pulls its programs; a program hit pulls its docs).

**Tech Stack:** Python stdlib only for the core (regex/heading parsing, `html.parser`); optional `pypdf` + `python-docx` behind a new `docs` pip extra; existing SQLite FTS5 + vector store; existing LLM client for the optional `doc_links` enrich pass.

---

## Design decisions (already approved — do not relitigate)

| Decision | Choice | Why |
| --- | --- | --- |
| Corpus (v1) | In-repo `.md .rst .adoc .txt .html` (stdlib) + `.pdf .docx` behind `pip install -e ".[docs]"` | Local-lite stays dependency-free; mainframe estates keep specs as PDFs |
| Linking | Deterministic mention-linker in `GraphBuilder`; optional LLM pass adds `llm-suggested` links | Graph-first philosophy; zero-LLM baseline must work |
| Surfacing | Retrieval + impact/symbol packs + dashboard `/docs` + MCP tools | All four requested |
| Default | **On** (`UCI_INDEX_DOCS=0` disables) | Docs are part of the codebase picture, like git metadata |
| Schema | One new entity kind `DOC_SECTION`, one new relation `DESCRIBES`. **No `DOCUMENT` entity** — a doc file's existing FILE/MODULE nodes are the document; sections ride under MODULE exactly like `PARAGRAPH` rides under `LEGACY_PROGRAM` | Avoids double-rooting; zero storage migration (generic kind/type columns) |
| Impact honesty | `DESCRIBES` never enters `DEPENDENCY_LIKE` / `RESOLVED_LEVELS` math; doc links never change risk scores or completeness | A README mentioning `COSGN00C` must not grow its blast radius |
| Gaps | Only **backticked/code-span** member-shaped mentions that resolve to nothing become gap records (`documented-artifact-missing`). Bare prose misses are dropped as noise | Honest but low-noise |

### Mention-resolution ladder (the heart)

| Rung | Evidence in doc | resolution label | confidence | Target resolution |
| --- | --- | --- | --- | --- |
| 1 | Path reference or markdown link to an indexed file (`app/cbl/COSGN00C.cbl`) | `doc-path` | 0.95 | FILE by path |
| 2 | Heading contains a resolvable artifact name (`## COSGN00C — Signon`) | `doc-heading` | 0.95 | symbol inventory |
| 3 | Backtick code-span (`` `COSGN00C` ``, `` `PricingCalculator.calculate` ``) | `doc-code-span` | 0.9 | qname, then name |
| 4 | Bare member-shaped token in prose/tables (`COSGN00C`, `CC00`) | `doc-mention` | 0.85 | name, unique only |

Guardrails (all rungs): unique match required (fan-out cap = existing `_FANOUT_CAP`); stub (`missing`/`external`) targets never linked; stoplist + `_SYSTEM_UTILITIES` + `gap_external_prefixes` excluded; `PARAGRAPH` targets excluded in v1; each edge carries `attributes.match`, `attributes.context` (the mention line, truncated), `resolution`, and provenance with the mention's line number.

### New configuration (every key ships in `.env.sample`, sanitized — never commit real values)

| Env var | Config field | Default | Meaning |
| --- | --- | --- | --- |
| `UCI_INDEX_DOCS` | `index_docs` | `1` (on) | Master switch for the doc pipeline |
| `UCI_WEIGHT_DOC` | `weight_doc` | `0.8` | Post-RRF multiplier on `DOC_SECTION` hits (docs must not swamp code) |
| `UCI_DOC_MAX_BYTES` | `doc_max_bytes` | `10_000_000` | Size cap for converter formats (PDF/DOCX) only |

---

## File structure

**Create:**
- `src/uci/parser/doc_parser.py` — DocParser: dialect-aware structure (headings→sections) + mention extraction
- `src/uci/ingest/docconvert.py` — optional-format converter registry (PDF/DOCX → text), content-hash-cached
- `src/uci/enrich/doc_links.py` — optional LLM linking pass (imported by `enricher.py`)
- `tests/test_doc_parser.py`, `tests/test_doc_linker.py`, `tests/test_docconvert.py`, `tests/test_docs_surfaces.py`
- `evals/docs_eval.py` + `evals/datasets/doc_links/carddemo.json` — doc-linkage eval track
- `docs/documentation-ingestion.md` — user guide (Task 15)
- `tests/fixtures/sample_repo/README.md`, `tests/fixtures/sample_repo/docs/pricing.md` — fixture docs

**Modify:**
- `src/uci/core/entities.py` — `DOC_SECTION` kind
- `src/uci/core/relationships.py` — `DESCRIBES` type (NOT in `DEPENDENCY_LIKE`)
- `src/uci/core/schema.py` — `RELATION_SPECS[DESCRIBES]`, aliases
- `src/uci/ingest/langdetect.py` — doc extensions/filenames, `is_doc()`, qname stripping
- `src/uci/ingest/scanner.py` — doc classification honoring `index_docs` + converter availability
- `src/uci/ingest/indexer.py` — converter read path, doc chunk gate, doc stats
- `src/uci/ingest/graph_builder.py` — `describes` link resolution + doc gap policy
- `src/uci/embeddings/chunking.py` — `DOC_SECTION` chunkable
- `src/uci/config.py` — 3 new fields; PDF un-ignore when docs active
- `src/uci/retrieval/hybrid.py` — doc weighting, `DESCRIBES` graph expansion, doc reason
- `src/uci/retrieval/impact.py` — `documentation` stratum
- `src/uci/engine.py` — `docs()` facade method; docs in `entity_detail`; `search_docs`/`get_documentation` support; capabilities
- `src/uci/mcp/tools.py` — 2 new tools + dispatch
- `src/uci/cli/main.py` — `uci docs` command
- `src/uci/api/server.py` + `src/uci/api/views.py` — `/docs` page, `/api/docs*` endpoints, nav entry
- `src/uci/enrich/enricher.py` — register `doc_links` pass
- `pyproject.toml` — `docs` extra; `all` extra grows
- `.env.sample` — new keys (sanitized)
- `README.md`, `docs/canonical-schema.md`, `docs/architecture.md`, `docs/retrieval-strategy.md`, `docs/mcp-tools.md`, `docs/dashboard.md`, `docs/llm-enrichment.md`, `docs/roadmap.md` — Task 15

---

## Task 0: Branch + baseline snapshot

- [x] **Step 0.1:** Create a feature branch (work never lands directly on `main`):

```bash
git checkout -b feat/doc-ingestion
```

- [x] **Step 0.2:** Capture the pre-change eval baseline (house rule: *no extraction change ships without an eval delta*):

```bash
PYTHONPATH=src python3 evals/run_eval.py --baseline evals/reports/baseline.json || true
PYTHONPATH=src python3 -m pytest -q   # expect: all pass before you start
```

Record the summary numbers in your working notes; Task 16 compares against them.

---

## Task 1: Schema — `DOC_SECTION` entity kind + `DESCRIBES` relation

**Files:**
- Modify: `src/uci/core/entities.py`
- Modify: `src/uci/core/relationships.py`
- Modify: `src/uci/core/schema.py`
- Test: `tests/test_core_schema.py`

- [x] **Step 1.1: Write failing tests** (append to `tests/test_core_schema.py`):

```python
from uci.core.entities import SYMBOL_KINDS, EntityType
from uci.core.relationships import DEPENDENCY_LIKE, RelationType
from uci.core.schema import RELATION_SPECS, normalize_entity, normalize_relation, validate_relationship


def test_doc_section_kind_exists_and_is_not_a_symbol():
    assert EntityType.DOC_SECTION.value == "doc_section"
    # sections must not win resolve_symbol over the code artifact they describe
    assert EntityType.DOC_SECTION not in SYMBOL_KINDS


def test_describes_relation_exists_and_never_drives_impact():
    assert RelationType.DESCRIBES.value == "describes"
    # a README mentioning a program must not inflate its blast radius
    assert RelationType.DESCRIBES not in DEPENDENCY_LIKE


def test_describes_relation_spec_allows_doc_sources():
    spec = RELATION_SPECS[RelationType.DESCRIBES]
    assert spec.directed
    assert EntityType.DOC_SECTION in spec.sources
    assert not spec.targets  # any target kind
    assert validate_relationship(
        RelationType.DESCRIBES, EntityType.DOC_SECTION, EntityType.LEGACY_PROGRAM
    ) == []


def test_doc_aliases_normalize():
    assert normalize_entity("doc") is EntityType.DOC_SECTION
    assert normalize_entity("doc_section") is EntityType.DOC_SECTION
    assert normalize_relation("documents") is RelationType.DESCRIBES
    assert normalize_relation("describes") is RelationType.DESCRIBES
```

- [x] **Step 1.2:** Run: `PYTHONPATH=src python3 -m pytest tests/test_core_schema.py -q` — expect FAIL (`AttributeError: DOC_SECTION`).

- [x] **Step 1.3: Implement.** In `entities.py`, add to `EntityType` after the legacy tier (keep the grouping comment style):

```python
    # --- Documentation ---
    DOC_SECTION = "doc_section"
```

Do **not** add it to `SYMBOL_KINDS` / `CALLABLE_KINDS` / `CONTAINER_KINDS`.

In `relationships.py`, add under the business/domain group:

```python
    # documentation
    DESCRIBES = "describes"
```

(Leave `DEPENDENCY_LIKE`, `DATA_FLOW`, `RESOLVED_LEVELS` untouched.)

In `schema.py`, add to `RELATION_SPECS`:

```python
    RelationType.DESCRIBES: RelationSpec(
        True,
        frozenset({EntityType.DOC_SECTION, EntityType.FILE, EntityType.MODULE}),
        frozenset(),  # any target: programs, jobs, tables, screens, files, functions…
    ),
```

and the aliases: `"doc": EntityType.DOC_SECTION`, `"section": EntityType.DOC_SECTION` in `_ENTITY_ALIASES`; `"documents": RelationType.DESCRIBES`, `"describe": RelationType.DESCRIBES` in `_RELATION_ALIASES`.

- [x] **Step 1.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_core_schema.py -q` — expect PASS.
- [x] **Step 1.5:** Commit:

```bash
git add src/uci/core/entities.py src/uci/core/relationships.py src/uci/core/schema.py tests/test_core_schema.py
git commit -m "feat(schema): DOC_SECTION entity kind + DESCRIBES relation (impact-neutral)"
```

---

## Task 2: Detection — doc languages, `is_doc()`, config switch

**Files:**
- Modify: `src/uci/ingest/langdetect.py`
- Modify: `src/uci/config.py`
- Modify: `src/uci/ingest/scanner.py`
- Modify: `.env.sample`
- Test: `tests/test_langanalyze.py` (append), `tests/test_config_env.py` (append)

- [x] **Step 2.1: Write failing tests.** Append to `tests/test_langanalyze.py`:

```python
from uci.ingest.langdetect import detect_language, is_doc, module_qname


def test_doc_extensions_detected():
    assert detect_language("README.md") == "markdown"
    assert detect_language("docs/guide.rst") == "rst"
    assert detect_language("notes/design.adoc") == "asciidoc"
    assert detect_language("CHANGES.txt") == "doctext"
    assert detect_language("site/index.html") == "htmldoc"
    assert detect_language("specs/layout.pdf") == "pdf"
    assert detect_language("specs/req.docx") == "docx"


def test_doc_filenames_without_extension_detected():
    assert detect_language("README") == "doctext"
    assert detect_language("sub/CHANGELOG") == "doctext"
    assert detect_language("LICENSE") is None  # licenses are noise, not docs


def test_is_doc_classifier():
    assert is_doc("markdown") and is_doc("pdf") and is_doc("doctext")
    assert not is_doc("python") and not is_doc("config") and not is_doc("text")


def test_doc_module_qname_strips_extension():
    assert module_qname("README.md") == "README"
    assert module_qname("docs/index.md") == "docs.index"
```

Append to `tests/test_config_env.py`:

```python
def test_index_docs_default_on_and_disable(tmp_path, monkeypatch):
    from uci.config import Config
    monkeypatch.delenv("UCI_INDEX_DOCS", raising=False)
    assert Config.from_env(tmp_path).index_docs is True
    monkeypatch.setenv("UCI_INDEX_DOCS", "0")
    assert Config.from_env(tmp_path).index_docs is False


def test_doc_weight_and_max_bytes_defaults(tmp_path):
    cfg = Config.from_env(tmp_path)
    assert cfg.weight_doc == 0.8
    assert cfg.doc_max_bytes == 10_000_000
```

- [x] **Step 2.2:** Run both test files — expect FAIL.
- [x] **Step 2.3: Implement `langdetect.py`.** Add doc mappings (distinct language ids so the registry can route one parser under many names, and the scanner can gate converter formats):

```python
_DOC_EXT_LANGUAGE: dict[str, str] = {
    ".md": "markdown", ".markdown": "markdown",
    ".rst": "rst",
    ".adoc": "asciidoc", ".asciidoc": "asciidoc",
    ".txt": "doctext",
    ".html": "htmldoc", ".htm": "htmldoc",
    ".pdf": "pdf", ".docx": "docx",
}
#: extensionless conventional doc filenames (basename match, case-sensitive by convention)
_DOC_FILENAMES = frozenset({"README", "CHANGELOG", "CONTRIBUTING", "INSTALL", "NOTICE", "TODO"})
#: doc languages that require a converter (binary source)
DOC_CONVERTER_LANGS = frozenset({"pdf", "docx"})
_DOC_LANGS = frozenset(_DOC_EXT_LANGUAGE.values())


def is_doc(language: str | None) -> bool:
    return language in _DOC_LANGS
```

In `detect_language()`, check `_DOC_EXT_LANGUAGE` after the existing `_EXT_LANGUAGE` loop, then the basename against `_DOC_FILENAMES` (return `"doctext"`). Keep `.html`/`.css` etc. behavior: **remove `.md`, `.rst`, `.txt`, `.html` from `_TEXT_EXTS`** (the doc pipeline owns them now; `.sql`, `.sh`, `.css` stay under `index_all_text`). In `module_qname()`, extend the strip-extension tuple with `(".markdown", ".md", ".rst", ".adoc", ".asciidoc", ".txt", ".html", ".htm", ".pdf", ".docx")`.

- [x] **Step 2.4: Implement `config.py`.** Add fields with defaults + env wiring in `from_env` (mirror `index_all_text`):

```python
    # documentation pipeline (docs/documentation-ingestion.md)
    index_docs: bool = True
    weight_doc: float = 0.8
    doc_max_bytes: int = 10_000_000
```

```python
            index_docs=str(env.get("UCI_INDEX_DOCS", "1")).lower() in _TRUE,
            weight_doc=_num(env, "UCI_WEIGHT_DOC", 0.8),
            doc_max_bytes=int(_num(env, "UCI_DOC_MAX_BYTES", 10_000_000)),
```

`DEFAULT_IGNORE_GLOBS` contains `*.pdf`: remove it from the constant and instead have `Config.from_env` append `*.pdf` and `*.docx` to `ignore_globs` **only when `index_docs` is false** (compute before constructing `cfg`; keep `ignore_globs` overrideable). This keeps `--no-docs` behavior identical to today.

- [x] **Step 2.5: Implement `scanner.py`.** In `_classify`, docs route through language detection already; add the gates: if `is_doc(language)` and not `config.index_docs` → return `None`; if `language in DOC_CONVERTER_LANGS` use `config.doc_max_bytes` (not `max_file_bytes`) for the size check and **skip the file (return None) when the converter is unavailable** (`from .docconvert import available; available(language)` — Task 3 provides it; until then guard with a try/except ImportError returning None). Note: `analyze_language(rp, read_head(abs_path))` returns `None` head for binary files — call `detect_language(rp)` first for converter formats so PDFs are classified by extension, not content.

- [x] **Step 2.6: Verify `.env.sample`** already contains the documentation-ingestion block below (it was added when this plan was written — do not duplicate it; extend only if a key is missing):

```ini
# --- Documentation ingestion (docs/documentation-ingestion.md) -----------------------
# Docs (.md/.rst/.adoc/.txt/.html; PDF/DOCX with `pip install -e ".[docs]"`) are indexed
# by default into the graph as DOC_SECTION entities linked to the code they describe.
UCI_INDEX_DOCS=1
UCI_WEIGHT_DOC=0.8          # ranking multiplier for doc hits (docs must not swamp code)
UCI_DOC_MAX_BYTES=10000000  # size cap for converter formats (PDF/DOCX) only
```

- [x] **Step 2.7:** Run: `PYTHONPATH=src python3 -m pytest tests/test_langanalyze.py tests/test_config_env.py -q` — expect PASS. Then the full suite (`PYTHONPATH=src python3 -m pytest -q`) — the `_TEXT_EXTS` change may surface `index_all_text` tests to adjust; fix expectations only where the test asserted `.md`→text behavior.
- [x] **Step 2.8:** Commit: `git commit -am "feat(ingest): doc language detection + UCI_INDEX_DOCS/UCI_WEIGHT_DOC/UCI_DOC_MAX_BYTES config"`

---

## Task 3: Converter registry — PDF/DOCX → text (optional extra)

**Files:**
- Create: `src/uci/ingest/docconvert.py`
- Modify: `src/uci/ingest/indexer.py` (read path)
- Modify: `pyproject.toml`
- Test: `tests/test_docconvert.py`

- [x] **Step 3.1: Write failing tests** (`tests/test_docconvert.py`). Converter tests must pass **without** the extra installed (the registry reports unavailability; conversion tests are skipped):

```python
import pytest

from uci.ingest.docconvert import available, extract_text


def test_unknown_language_unavailable():
    assert not available("markdown")   # only converter formats live here
    assert not available("nope")


def test_extract_text_returns_none_when_unavailable(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    if available("pdf"):
        pytest.skip("pypdf installed; covered by test_extract_pdf_roundtrip")
    assert extract_text(str(p), "pdf", max_bytes=10_000_000) is None


@pytest.mark.optional_backend
def test_extract_docx_roundtrip(tmp_path):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_heading("Payments Spec", level=1)
    doc.add_paragraph("COSGN00C validates users.")
    p = tmp_path / "spec.docx"
    doc.save(p)
    text = extract_text(str(p), "docx", max_bytes=10_000_000)
    assert "# Payments Spec" in text and "COSGN00C" in text
```

- [x] **Step 3.2:** Run: `PYTHONPATH=src python3 -m pytest tests/test_docconvert.py -q` — expect FAIL (module missing).
- [x] **Step 3.3: Implement `docconvert.py`** — lazy imports, never raises, honest markers:

```python
"""Optional binary-document converters (PDF/DOCX -> text).

Adapters import their library lazily (pyproject extra ``docs``); a missing library means
``available() -> False`` and the scanner simply skips the file — never an error. Extracted
text is deterministic for a given file so the content-hash incremental path works unchanged.
DOCX headings are rendered as markdown ``#`` lines so the markdown section parser applies;
PDF pages are separated by an explicit marker line the doc parser turns into per-page sections.
"""

from __future__ import annotations

from pathlib import Path

PAGE_MARKER = "\f[uci-page {n}]"  # form-feed + marker line, one per PDF page


def available(language: str) -> bool:
    if language == "pdf":
        try:
            import pypdf  # noqa: F401
            return True
        except ImportError:
            return False
    if language == "docx":
        try:
            import docx  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def extract_text(abs_path: str, language: str, max_bytes: int) -> str | None:
    """Extract plain text, or ``None`` (unavailable converter, oversized, or extraction error)."""
    p = Path(abs_path)
    try:
        if p.stat().st_size > max_bytes:
            return None
    except OSError:
        return None
    if not available(language):
        return None
    try:
        if language == "pdf":
            return _pdf_text(p)
        if language == "docx":
            return _docx_text(p)
    except Exception:  # extraction must never break indexing
        return None
    return None


def _pdf_text(p: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(p))
    pages = []
    for n, page in enumerate(reader.pages, start=1):
        pages.append(PAGE_MARKER.replace("{n}", str(n)))
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _docx_text(p: Path) -> str:
    import docx
    doc = docx.Document(str(p))
    lines: list[str] = []
    for para in doc.paragraphs:
        style = (para.style.name or "").lower() if para.style else ""
        if style.startswith("heading"):
            try:
                level = max(1, min(6, int(style.rsplit(" ", 1)[-1])))
            except ValueError:
                level = 2
            lines.append(f"{'#' * level} {para.text}".rstrip())
        else:
            lines.append(para.text)
    return "\n".join(lines)


__all__ = ["available", "extract_text", "PAGE_MARKER"]
```

- [x] **Step 3.4: Wire the indexer read path.** In `indexer.py` inside the scan loop, replace the plain `read_text` call for converter formats:

```python
        for sf in scanned:
            if sf.language in DOC_CONVERTER_LANGS:
                source = extract_text(sf.abs_path, sf.language, self.config.doc_max_bytes)
            else:
                source = read_text(sf.abs_path, self.config.max_file_bytes)
            if source is None:
                continue
```

(imports: `from .docconvert import extract_text` and `from .langdetect import DOC_CONVERTER_LANGS, is_doc` — `is_doc` used in Task 7). Content-hash change detection then hashes the *extracted text* — deterministic, so incremental embedding still works; no separate conversion cache is needed (YAGNI: extraction re-runs per index pass, matching how parsing re-runs per pass).

- [x] **Step 3.5: `pyproject.toml`.** Add the extra and grow `all`:

```toml
# Documentation converters for the doc-ingestion pipeline (PDF/DOCX; docs/documentation-ingestion.md).
docs = ["pypdf>=4.0", "python-docx>=1.1"]
```

and append `"pypdf>=4.0", "python-docx>=1.1"` to the `all` list.

- [x] **Step 3.6:** Run: `PYTHONPATH=src python3 -m pytest tests/test_docconvert.py -q` — expect PASS (docx test skipped unless installed).
- [x] **Step 3.7:** Commit: `git commit -am "feat(ingest): optional PDF/DOCX converter registry behind [docs] extra"`

---

## Task 4: DocParser — structure (sections) for Markdown

**Files:**
- Create: `src/uci/parser/doc_parser.py`
- Modify: `src/uci/parser/registry.py`
- Test: `tests/test_doc_parser.py`

The parser is a *dumb structural extractor* (house style): sections + mention links, line-accurate, never raises.

- [x] **Step 4.1: Write failing structure tests** (`tests/test_doc_parser.py`):

```python
from uci.core.entities import EntityType
from uci.parser.doc_parser import DocParser

MD = """\
# CardDemo Overview

CardDemo is a credit card system.

## Signon — COSGN00C

The `COSGN00C` program validates users against USRSEC.
See [the source](app/cbl/COSGN00C.cbl) and transaction CC00.

## Batch

Jobs run nightly.
"""


def _parse(text, path="README.md", lang="markdown", qname="README"):
    p = DocParser()
    p.language = lang
    return p.parse(text, path, qname)


def test_markdown_sections_with_line_spans():
    result = _parse(MD)
    secs = [s for s in result.symbols if s.kind is EntityType.DOC_SECTION]
    names = [s.name for s in secs]
    assert names == ["CardDemo Overview", "Signon — COSGN00C", "Batch"]
    signon = secs[1]
    assert signon.qualified_name == "README.signon-cosgn00c"
    assert signon.start_line == 5 and signon.end_line == 9
    assert signon.attributes["level"] == 2


def test_sections_never_raise_on_garbage():
    result = _parse("\x01\x02 not really markdown \n#\n###   \n")
    assert result.errors == [] or all(isinstance(e, str) for e in result.errors)


def test_headingless_doc_gets_one_whole_file_section():
    result = _parse("just prose\nmore prose\n", path="NOTES.txt", lang="doctext", qname="NOTES")
    secs = [s for s in result.symbols if s.kind is EntityType.DOC_SECTION]
    assert len(secs) == 1 and secs[0].name == "NOTES" and secs[0].start_line == 1
```

- [x] **Step 4.2:** Run: `PYTHONPATH=src python3 -m pytest tests/test_doc_parser.py -q` — FAIL (no module).
- [x] **Step 4.3: Implement structure extraction.** Core shape of `doc_parser.py` (mention extraction arrives in Task 5 — leave `_extract_mentions` returning `[]` for now):

```python
"""Documentation parser: headings -> DOC_SECTION symbols; mentions -> ParsedLink("describes").

Like every parser here it is a dumb structural extractor: line-accurate records, no resolution
(the graph builder owns the honesty ladder), never raises on malformed input. One parser class
serves all doc dialects; the registry registers one instance per language id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.entities import EntityType
from .base import LanguageParser, ParsedLink, ParsedSymbol, ParseResult

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT_UNDERLINE = re.compile(r"^(=+|-+)\s*$")           # markdown/rst setext-style
_ADOC_HEADING = re.compile(r"^(={1,6})\s+(.+?)\s*$")
_HTML_HEADING = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_PAGE = re.compile(r"^\f\[uci-page (\d+)\]$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_STRIP.sub("-", text.lower()).strip("-")[:60] or "section"


@dataclass
class _Heading:
    line: int
    level: int
    title: str
    page: int | None = None


class DocParser(LanguageParser):
    language = "markdown"   # instances are registered per dialect id

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        try:
            lines = source.split("\n")
            headings = self._headings(lines)
            sections = self._sections(headings, len(lines), module_qname, path)
            result.symbols.extend(sections)
            result.links.extend(self._extract_mentions(lines, sections))
        except Exception as exc:  # contract: never raise
            result.errors.append(f"doc-parse: {exc}")
        return result

    # -- structure ----------------------------------------------------------
    def _headings(self, lines: list[str]) -> list[_Heading]:
        if self.language == "htmldoc":
            return self._html_headings(lines)
        out: list[_Heading] = []
        page = None
        for i, raw in enumerate(lines, start=1):
            pm = _PAGE.match(raw)
            if pm:  # PDF page marker -> synthetic per-page heading
                page = int(pm.group(1))
                out.append(_Heading(i, 1, f"Page {page}", page))
                continue
            if self.language in ("markdown", "doctext", "pdf", "docx"):
                m = _MD_HEADING.match(raw)
                if m:
                    out.append(_Heading(i, len(m.group(1)), m.group(2).strip(), page))
                    continue
            if self.language == "asciidoc":
                m = _ADOC_HEADING.match(raw)
                if m:
                    out.append(_Heading(i, len(m.group(1)), m.group(2).strip(), page))
                    continue
            if self.language in ("rst", "markdown", "doctext"):
                # setext: a text line underlined by === or ---
                if i < len(lines) and lines[i].strip() and _SETEXT_UNDERLINE.match(lines[i]) \
                        and raw.strip() and not raw.startswith(("#", "=", "-", " ")):
                    level = 1 if lines[i].lstrip().startswith("=") else 2
                    out.append(_Heading(i, level, raw.strip(), page))
        return out

    def _html_headings(self, lines: list[str]) -> list[_Heading]:
        out: list[_Heading] = []
        for i, raw in enumerate(lines, start=1):
            for m in _HTML_HEADING.finditer(raw):
                title = _TAG.sub("", m.group(2)).strip()
                if title:
                    out.append(_Heading(i, int(m.group(1)), title))
        return out

    def _sections(self, headings: list[_Heading], n_lines: int,
                  module_qname: str, path: str) -> list[ParsedSymbol]:
        title = module_qname.rsplit(".", 1)[-1]
        if not headings:
            return [ParsedSymbol(
                name=title, qualified_name=f"{module_qname}", kind=EntityType.DOC_SECTION,
                start_line=1, end_line=max(1, n_lines),
                attributes={"level": 0, "heading": title},
            )]
        out: list[ParsedSymbol] = []
        seen: dict[str, int] = {}
        for idx, h in enumerate(headings):
            end = (headings[idx + 1].line - 1) if idx + 1 < len(headings) else n_lines
            slug = _slug(h.title)
            seen[slug] = seen.get(slug, 0) + 1
            if seen[slug] > 1:
                slug = f"{slug}-{seen[slug]}"
            attrs: dict = {"level": h.level, "heading": h.title}
            if h.page is not None:
                attrs["page"] = h.page
            out.append(ParsedSymbol(
                name=h.title, qualified_name=f"{module_qname}.{slug}",
                kind=EntityType.DOC_SECTION, start_line=h.line, end_line=max(h.line, end),
                attributes=attrs,
            ))
        return out

    # -- mentions (Task 5) ----------------------------------------------------
    def _extract_mentions(self, lines: list[str], sections: list[ParsedSymbol]) -> list[ParsedLink]:
        return []
```

- [x] **Step 4.4: Register dialects** in `registry.py`:

```python
from .doc_parser import DocParser

for _lang in ("markdown", "rst", "asciidoc", "doctext", "htmldoc", "pdf", "docx"):
    _p = DocParser()
    _p.language = _lang
    register(_p)
```

- [x] **Step 4.5:** Run: `PYTHONPATH=src python3 -m pytest tests/test_doc_parser.py -q` — PASS. Full suite still green.
- [x] **Step 4.6:** Commit: `git commit -am "feat(parser): DocParser — heading-bounded DOC_SECTION extraction for md/rst/adoc/txt/html/pdf/docx"`

---

## Task 5: DocParser — mention extraction (paths, code-spans, headings, bare members)

**Files:**
- Modify: `src/uci/parser/doc_parser.py`
- Test: `tests/test_doc_parser.py` (append)

The parser only *extracts and classifies* mention candidates; it does **not** resolve them (graph builder's job). Every mention becomes `ParsedLink(relation="describes", src_qname=<section>, target_name=..., attributes={"match": ..., "context": ...})`.

- [x] **Step 5.1: Write failing mention tests** (append to `tests/test_doc_parser.py`):

```python
def _mentions(result):
    return {(l.src_qname, l.target_name, l.attributes["match"]) for l in result.links}


def test_code_span_and_path_and_bare_mentions():
    result = _parse(MD)
    m = _mentions(result)
    assert ("README.signon-cosgn00c", "COSGN00C", "code-span") in m
    assert ("README.signon-cosgn00c", "app/cbl/COSGN00C.cbl", "path") in m
    assert ("README.signon-cosgn00c", "CC00", "bare") in m
    assert ("README.signon-cosgn00c", "USRSEC", "bare") in m


def test_heading_mentions_flagged():
    result = _parse(MD)
    m = _mentions(result)
    assert ("README.signon-cosgn00c", "COSGN00C", "heading") in m


def test_stoplist_and_short_tokens_skipped():
    text = "# T\n\nCOBOL and CICS and JCL run IT with `SORT`.\n"
    result = _parse(text)
    names = {l.target_name for l in result.links}
    assert "COBOL" not in names and "CICS" not in names and "IT" not in names


def test_qualified_names_and_fenced_blocks():
    text = (
        "# T\n\nUse `pricing.calculator.PricingCalculator.calculate` here.\n"
        "```cobol\nCALL 'CBTRN01C'\nMOVE X TO Y\n```\n"
    )
    result = _parse(text)
    m = _mentions(result)
    assert ("T.t", "pricing.calculator.PricingCalculator.calculate", "code-span") in m
    # fenced code blocks are quoted code, not prose mentions: no 'bare' links from inside
    assert not any(l.attributes["match"] == "bare" and l.target_name in ("CBTRN01C", "MOVE", "CALL")
                   for l in result.links)
```

(Adjust the first section qname in the last test to the actual slug of `# T` → `T.t`.)

- [x] **Step 5.2:** Run — expect FAIL.
- [x] **Step 5.3: Implement mention extraction** in `doc_parser.py` (replace the stub):

```python
_CODE_SPAN = re.compile(r"`([^`\n]{2,120})`")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_PATHISH = re.compile(r"^[\w./-]+\.[A-Za-z0-9]{1,8}$")
#: mainframe member shape: 3-8 chars, starts alphabetic/national, uppercase
_MEMBER = re.compile(r"\b([A-Z@#$][A-Z0-9@#$]{2,7})\b")
#: dotted qualified name (modern code) inside a code span
_QNAME = re.compile(r"^[A-Za-z_][\w]*(\.[\w]+)+$")
_FENCE = re.compile(r"^(```|~~~)")

#: ALL-CAPS prose words that are never code artifacts (platform nouns, English)
_MENTION_STOPLIST = frozenset({
    "COBOL", "CICS", "JCL", "VSAM", "HLASM", "BMS", "RACF", "IMS", "MQ", "DB2", "SQL",
    "AWS", "API", "HTTP", "HTTPS", "JSON", "YAML", "XML", "CSV", "PDF", "README",
    "TODO", "NOTE", "WARNING", "IMPORTANT", "LICENSE", "CHANGELOG", "AND", "THE",
    "FOR", "NOT", "ALL", "ANY", "NEW", "OLD", "SET", "GET", "PUT", "RUN", "USE",
    "GDG", "PDS", "KSDS", "ESDS", "RRDS", "COMP", "FTP", "TXT",
})


def _section_at(sections: list[ParsedSymbol], line: int) -> ParsedSymbol | None:
    for sec in sections:
        if sec.start_line <= line <= sec.end_line:
            return sec
    return sections[0] if sections else None


class DocParser(LanguageParser):
    ...
    def _extract_mentions(self, lines: list[str], sections: list[ParsedSymbol]) -> list[ParsedLink]:
        links: list[ParsedLink] = []
        seen: set[tuple[str, str, str]] = set()
        in_fence = False

        def add(section: ParsedSymbol | None, target: str, match: str, line_no: int, context: str):
            if section is None or not target:
                return
            key = (section.qualified_name, target, match)
            if key in seen:
                return
            seen.add(key)
            links.append(ParsedLink(
                relation="describes", src_qname=section.qualified_name,
                target_name=target, target_kind=EntityType.LEGACY_PROGRAM.value,
                start_line=line_no,
                attributes={"match": match, "context": context.strip()[:160]},
            ))

        for i, raw in enumerate(lines, start=1):
            if _FENCE.match(raw.strip()):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            section = _section_at(sections, i)
            heading_line = section is not None and i == section.start_line

            for m in _MD_LINK.finditer(raw):
                target = m.group(1).split("#", 1)[0].lstrip("./")
                if target and _PATHISH.match(target):
                    add(section, target, "path", i, raw)
            for m in _CODE_SPAN.finditer(raw):
                span = m.group(1).strip()
                if "/" in span and _PATHISH.match(span):
                    add(section, span.lstrip("./"), "path", i, raw)
                elif _QNAME.match(span):
                    add(section, span, "code-span", i, raw)
                elif _MEMBER.fullmatch(span) and span not in _MENTION_STOPLIST:
                    add(section, span, "code-span", i, raw)
            plain = _CODE_SPAN.sub(" ", raw)  # bare tokens: outside code spans only
            for m in _MEMBER.finditer(plain):
                token = m.group(1)
                if token in _MENTION_STOPLIST:
                    continue
                add(section, token, "heading" if heading_line else "bare", i, raw)
        return links
```

- [x] **Step 5.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_doc_parser.py -q` — PASS.
- [x] **Step 5.5:** Commit: `git commit -am "feat(parser): doc mention extraction — paths, code-spans, headings, bare member tokens"`

---

## Task 6: GraphBuilder — resolve `describes` links (the honesty ladder)

**Files:**
- Modify: `src/uci/ingest/graph_builder.py`
- Test: `tests/test_doc_linker.py`

Resolution policy (deterministic, mirrors calls):
- Confidence by match: `path` 0.95 · `heading` 0.95 · `code-span` 0.9 · `bare` 0.85; `resolution` label `doc-path` / `doc-heading` / `doc-code-span` / `doc-mention`.
- Targets tried in order: (a) `path` → FILE entity by repo-relative path; (b) dotted qname → `by_qname` (prefer SYMBOL_KINDS); (c) name → `by_name` with kind preference `LEGACY_PROGRAM, JCL_JOB, TRANSACTION_CODE, COPYBOOK, SCREEN, DATASET, DATABASE_TABLE, FUNCTION, CLASS, MODULE`; **unique within the preferred kind** or no edge.
- Never link stubs (`missing`/`external` attributes) or `PARAGRAPH`s; never self-link; respect `_FANOUT_CAP`.
- Gap policy: only `match == "code-span"` member-shaped misses (and not external-prefixed / system utilities) → `report_gap("documented-artifact", name, EntityType.LEGACY_PROGRAM, prov, "documented-artifact-missing", f"{name} (referenced in documentation)")` **without** emitting an edge to the stub? **No — follow house style: emit the DESCRIBES edge to the stub with `resolution="missing"`** so the gaps panel and doc page can show it, exactly like unresolved COPY members.
- `bare`/`heading`/`path` misses: drop silently (noise).

- [ ] **Step 6.1: Write failing tests** (`tests/test_doc_linker.py`) — build a tiny in-memory repo through the public pipeline. Put the `_repo`/`_doc_repo_engine` helpers in `tests/conftest.py` (Tasks 8–10 and 12 reuse them; keep tests DRY):

```python
from pathlib import Path

from uci import Config, Engine
from uci.core.entities import EntityType
from uci.core.relationships import RelationType

COBOL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. COSGN00C.
       PROCEDURE DIVISION.
       MAIN-PARA.
           MOVE 1 TO X.
"""

README = """\
# App

## Signon — COSGN00C

`COSGN00C` handles signon. See [source](cbl/COSGN00C.cbl).
Missing member `CBTRN99C` is documented but absent. COBOL rules IGNOREME.
"""


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "cbl").mkdir()
    (tmp_path / "cbl" / "COSGN00C.cbl").write_text(COBOL)
    (tmp_path / "README.md").write_text(README)
    return tmp_path


def _describes(engine):
    return [r for r in engine.graph.relationships(RelationType.DESCRIBES)]


def test_doc_section_entities_and_describes_edges(tmp_path):
    with Engine(Config.from_env(_repo(tmp_path))) as eng:
        eng.index(full=True)
        sections = list(eng.graph.entities(kind=EntityType.DOC_SECTION))
        assert {s.name for s in sections} == {"App", "Signon — COSGN00C"}
        edges = _describes(eng)
        by = {(eng.graph.get_entity(r.src_id).name, eng.graph.get_entity(r.dst_id).name,
               r.attributes.get("resolution")) for r in edges}
        assert ("Signon — COSGN00C", "COSGN00C", "doc-heading") in by
        assert ("Signon — COSGN00C", "COSGN00C.cbl", "doc-path") in by


def test_documented_but_missing_member_becomes_gap(tmp_path):
    with Engine(Config.from_env(_repo(tmp_path))) as eng:
        eng.index(full=True)
        gaps = eng.gaps()["gaps"]
        names = {g["name"] for g in gaps}
        assert "CBTRN99C" in names
        gap = next(g for g in gaps if g["name"] == "CBTRN99C")
        assert "documented-artifact-missing" in gap["reasons"]
        assert "IGNOREME" not in names  # bare-prose miss: dropped, not a gap


def test_describes_never_inflates_impact(tmp_path):
    with Engine(Config.from_env(_repo(tmp_path))) as eng:
        eng.index(full=True)
        pack = eng.impact("COSGN00C")
        callers = [c["name"] for c in pack["impact"]["callers"]["resolved"]]
        assert all("Signon" not in c for c in callers)
```

(Adapt accessors: `Engine` exposes `graph` via its store wiring — check `engine.py` attribute name (`self.graph`) and use the real one; if not public, assert through `eng.entity_detail`/`eng.search` surfaces instead.)

- [ ] **Step 6.2:** Run — FAIL (no `describes` in `_LINK_RELATIONS`).
- [ ] **Step 6.3: Implement in `graph_builder.py`:**

1. `_LINK_RELATIONS["describes"] = RelationType.DESCRIBES`
2. `_LINK_SRC_KINDS["describes"] = (EntityType.DOC_SECTION,)`
3. In `_resolve_links`, branch **before** the generic member resolution:

```python
                if link.relation == "describes":
                    self._resolve_doc_link(link, src, fp)
                    continue
```

4. Add the resolver + helpers (module-level constants near `_SYSTEM_UTILITIES`):

```python
_DOC_CONFIDENCE = {"path": 0.95, "heading": 0.95, "code-span": 0.9, "bare": 0.85}
_DOC_RESOLUTION = {"path": "doc-path", "heading": "doc-heading",
                   "code-span": "doc-code-span", "bare": "doc-mention"}
_DOC_TARGET_KINDS = (
    EntityType.LEGACY_PROGRAM, EntityType.JCL_JOB, EntityType.TRANSACTION_CODE,
    EntityType.COPYBOOK, EntityType.SCREEN, EntityType.DATASET, EntityType.DATABASE_TABLE,
    EntityType.FUNCTION, EntityType.CLASS, EntityType.MODULE,
)
_MEMBER_SHAPE = re.compile(r"^[A-Z@#$][A-Z0-9@#$]{2,7}$")
```

```python
    def _resolve_doc_link(self, link, src: Entity, fp: FileParse) -> None:
        """Resolve one doc mention through the doc ladder. Unique real target -> DESCRIBES edge;
        code-span member miss -> documented-artifact gap + stub edge; other misses -> dropped."""
        match = link.attributes.get("match", "bare")
        confidence = _DOC_CONFIDENCE.get(match, 0.8)
        resolution = _DOC_RESOLUTION.get(match, "doc-mention")
        prov = Provenance(self.repo_id, fp.path, link.start_line, link.start_line,
                          f"{fp.language}_parser", confidence)
        attrs = {"resolution": resolution, "match": match,
                 "context": link.attributes.get("context", "")}
        target = self._doc_target(link.target_name, match)
        if target is not None:
            if target.id != src.id:
                self._add_rel(RelationType.DESCRIBES, src.id, target.id, prov, attrs)
            return
        name = link.target_name.upper()
        if match == "code-span" and _MEMBER_SHAPE.match(name) \
                and not self._is_external_name(name):
            stub = self.report_gap("documented-artifact", name, EntityType.LEGACY_PROGRAM,
                                   prov, "documented-artifact-missing",
                                   f"{name} (referenced in documentation)", external=False)
            self._add_rel(RelationType.DESCRIBES, src.id, stub.id, prov,
                          {**attrs, "resolution": "missing"})

    def _doc_target(self, name: str, match: str) -> Entity | None:
        """Unique, real (non-stub) target for a doc mention, or None."""
        if match == "path" or "/" in name:
            ent = self.entities.get(entity_id(EntityType.FILE, self.repo_id, name, name))
            return ent
        if "." in name and not _MEMBER_SHAPE.match(name):   # dotted qualified name
            cands = [e for e in self.by_qname.get(name, [])
                     if not e.attributes.get("missing") and not e.attributes.get("external")]
            pref = [e for e in cands if e.kind in SYMBOL_KINDS]
            pool = pref or cands
            return pool[0] if len(pool) == 1 else None
        cands = [e for e in self.by_name.get(name.lower(), [])
                 if e.kind in _DOC_TARGET_KINDS
                 and not e.attributes.get("missing") and not e.attributes.get("external")]
        if not cands or len(cands) > _FANOUT_CAP:
            return None
        for kind in _DOC_TARGET_KINDS:      # priority order, unique within the winning kind
            of_kind = [e for e in cands if e.kind == kind]
            if len(of_kind) == 1:
                return of_kind[0]
            if len(of_kind) > 1:
                return None
        return None
```

(`import re` is not currently in `graph_builder.py` — add it; `SYMBOL_KINDS` is already imported.)

- [ ] **Step 6.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_doc_linker.py -q` — PASS. Full suite green (existing eval-sensitive tests must not change: doc edges are additive).
- [ ] **Step 6.5:** Commit: `git commit -am "feat(graph): deterministic doc->code DESCRIBES resolution + documented-artifact gaps"`

---

## Task 7: Indexer + chunking — doc sections into FTS/vectors

**Files:**
- Modify: `src/uci/embeddings/chunking.py` (`_CHUNKABLE`)
- Modify: `src/uci/ingest/indexer.py` (chunk gate, stats)
- Test: `tests/test_chunking.py`, `tests/test_indexer.py` (append)

- [ ] **Step 7.1: Failing tests.** Append to `tests/test_chunking.py`:

```python
def test_doc_sections_are_chunkable():
    from uci.core.entities import EntityType
    from uci.embeddings.chunking import _CHUNKABLE
    assert EntityType.DOC_SECTION in _CHUNKABLE
```

Append to `tests/test_indexer.py` (reuse the doc fixture idea from Task 6):

```python
def test_doc_files_are_chunked_and_secret_scrubbed(tmp_path):
    from uci import Config, Engine
    (tmp_path / "README.md").write_text(
        "# Guide\n\nSet api_key = 'sk-supersecret1234567890' to connect.\n")
    with Engine(Config.from_env(tmp_path)) as eng:
        stats = eng.index(full=True)
        assert stats.chunks >= 1
        texts = [c["text"] for c in eng.metadata.iter_chunks(stats.repo_id)
                 if c["path"] == "README.md"]
        assert texts and all("supersecret" not in t for t in texts)
        assert any(c["kind"] == "doc_section" for c in eng.metadata.iter_chunks(stats.repo_id))


def test_index_docs_off_skips_docs(tmp_path):
    from uci import Config, Engine
    (tmp_path / "README.md").write_text("# Guide\n\nhello\n")
    with Engine(Config.from_env(tmp_path, {"index_docs": False})) as eng:
        stats = eng.index(full=True)
        assert all(c["path"] != "README.md" for c in eng.metadata.iter_chunks(stats.repo_id))
```

(Confirm `Engine` exposes `metadata`; otherwise assert via `eng.search("supersecret")` returning nothing and `eng.search("Guide")` hitting the section.)

- [ ] **Step 7.2:** Run — FAIL.
- [ ] **Step 7.3: Implement.** `chunking.py`: add `EntityType.DOC_SECTION` to `_CHUNKABLE`. `indexer.py`: change the chunk gate

```python
        for fp in file_parses:
            if not (is_code(fp.path) or is_doc(fp.language)):
                continue
```

and extend the embedding-model-guard line to re-embed docs too: `changed |= {fp.path for fp in file_parses if is_code(fp.path) or is_doc(fp.language)}`. Add `IndexStats.doc_sections: int = 0` and `IndexStats.doc_links: int = 0`; count after `builder.build(...)`:

```python
        stats.doc_sections = sum(1 for e in entities if e.kind is EntityType.DOC_SECTION)
        stats.doc_links = sum(1 for r in relationships if r.type is RelationType.DESCRIBES)
```

- [ ] **Step 7.4:** Run both test files, then the whole suite — PASS. Doc chunks now flow to FTS5 + vectors with the existing secret scrubber (`_make_chunk` already scrubs).
- [ ] **Step 7.5:** Commit: `git commit -am "feat(index): chunk+embed DOC_SECTIONs; doc counters in IndexStats"`

---

## Task 8: Retrieval — doc weighting, doc reason, DESCRIBES graph expansion

**Files:**
- Modify: `src/uci/retrieval/hybrid.py`
- Test: `tests/test_retrieval.py` (append)

- [ ] **Step 8.1: Failing tests** (append to `tests/test_retrieval.py`; use a tmp repo with one program + one README describing it, as in Task 6):

```python
def test_doc_hits_labeled_and_weighted(tmp_path):
    eng = _doc_repo_engine(tmp_path)   # helper: COBOL program + README, indexed
    res = eng.search("signon validates users")
    hits = res["hits"]
    doc_hits = [h for h in hits if h["kind"] == "doc_section"]
    assert doc_hits, "doc sections must be searchable"
    assert any("Documentation" in h["reason"] for h in doc_hits)


def test_doc_weight_zero_suppresses_docs(tmp_path):
    eng = _doc_repo_engine(tmp_path, overrides={"weight_doc": 0.0})
    res = eng.search("signon validates users")
    assert all(h["kind"] != "doc_section" for h in res["hits"])


def test_graph_expansion_bridges_docs_and_code(tmp_path):
    eng = _doc_repo_engine(tmp_path)
    res = eng.search("Signon — COSGN00C")   # doc-heading query seeds the section
    kinds = {h["kind"] for h in res["hits"]}
    assert "legacy_program" in kinds        # DESCRIBES expansion pulled the program
```

- [ ] **Step 8.2:** Run — FAIL (weighting/reason/expansion missing).
- [ ] **Step 8.3: Implement in `hybrid.py`:**

1. `_REASONS["doc"] = "Documentation describes this / matches the query"` and put `"doc"` after `"symbol"` in `_SIGNAL_PRIORITY`.
2. Graph expansion: in `_graph_signal`, extend `rtypes = list(DEPENDENCY_LIKE | {RelationType.DEFINES, RelationType.DESCRIBES})`.
3. Weight + label in `search()` before hits are built — after `scores` are fused:

```python
        for eid in list(scores):
            entity = self.graph.get_entity(eid)
            if entity is not None and entity.kind is EntityType.DOC_SECTION:
                if self.config.weight_doc <= 0:
                    scores.pop(eid)
                    continue
                scores[eid] *= self.config.weight_doc
                membership.setdefault(eid, [])
                if "doc" not in membership[eid]:
                    membership[eid].append("doc")
```

(`EntityType` is already imported.) The `reason` resolution then reports "Documentation…" whenever `doc` is the highest-priority signal on the hit and no symbol match beat it.

- [ ] **Step 8.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_retrieval.py -q` — PASS; full suite green (fusion changes can shift existing ranking tests — if one flips, check whether a doc hit legitimately outranked; adjust only with justification in the commit message).
- [ ] **Step 8.5:** Commit: `git commit -am "feat(retrieval): doc-section weighting (UCI_WEIGHT_DOC), doc reason, DESCRIBES expansion"`

---

## Task 9: Impact & symbol packs — the `documentation` stratum

**Files:**
- Modify: `src/uci/retrieval/impact.py`
- Modify: `src/uci/engine.py` (`entity_detail`, `find_symbol`)
- Test: `tests/test_impact.py`, `tests/test_docs_surfaces.py` (create)

- [ ] **Step 9.1: Failing tests** (`tests/test_docs_surfaces.py`):

```python
def test_impact_pack_includes_documentation(tmp_path):
    eng = _doc_repo_engine(tmp_path)
    pack = eng.impact("COSGN00C")
    docs = pack["impact"]["documentation"]
    assert docs and docs[0]["heading"].startswith("Signon")
    assert docs[0]["path"] == "README.md" and docs[0]["resolution"] == "doc-heading"
    # risk unchanged by docs: compare against a docless twin
    eng2 = _docless_twin_engine(tmp_path)
    assert pack["impact"]["risk"] == eng2.impact("COSGN00C")["impact"]["risk"]


def test_entity_detail_lists_documentation(tmp_path):
    eng = _doc_repo_engine(tmp_path)
    sym = eng.find_symbol("COSGN00C")["matches"][0]
    detail = eng.entity_detail(sym["entity_id"])
    assert any(d["path"] == "README.md" for d in detail.get("documentation", []))
```

- [ ] **Step 9.2:** Run — FAIL (KeyError `documentation`).
- [ ] **Step 9.3: Implement.** In `impact.py`, inside the pack builder where `tests`/`config`/`data` are gathered, collect in-edges of type `DESCRIBES` on the target (and on the target's FILE/MODULE — resolve via `provenance.path`), emitting dicts:

```python
    def _documentation(self, target: Entity) -> list[dict]:
        """Doc sections describing the target (direct, or via its file/module). Never affects risk."""
        out, seen = [], set()
        ids = [target.id]
        for other in self.graph.entities(repo_id=target.provenance.repo_id):  # file/module twins
            if other.provenance.path == target.provenance.path and \
                    other.kind in (EntityType.FILE, EntityType.MODULE) and other.id != target.id:
                ids.append(other.id)
        for eid in ids:
            for rel in self.graph.in_relationships(eid, [RelationType.DESCRIBES]):
                sec = self.graph.get_entity(rel.src_id)
                if sec is None or sec.id in seen or rel.attributes.get("resolution") == "missing":
                    continue
                seen.add(sec.id)
                out.append({
                    "entity_id": sec.id, "heading": sec.attributes.get("heading", sec.name),
                    "path": sec.provenance.path, "start_line": sec.provenance.start_line,
                    "end_line": sec.provenance.end_line,
                    "resolution": rel.attributes.get("resolution", ""),
                    "confidence": rel.provenance.confidence,
                    "context": rel.attributes.get("context", ""),
                })
        out.sort(key=lambda d: -d["confidence"])
        return out[:10]
```

**Performance note:** the file/module-twin scan above is O(entities) per call, matching existing `_proximity_signal` style; acceptable for v1 (impact is a single-symbol query). Wire `"documentation": self._documentation(target)` into the pack dict (alongside `tests`/`config`/`data`) and confirm the risk formula reads none of it. In `engine.py:entity_detail`, add the same list under `"documentation"` (reuse the analyzer's helper via `self._impact()._documentation(entity)` or duplicate the 15 lines if the analyzer isn't reachable — prefer reuse).

- [ ] **Step 9.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_docs_surfaces.py tests/test_impact.py -q` — PASS.
- [ ] **Step 9.5:** Commit: `git commit -am "feat(impact): documentation stratum in impact packs + entity detail (risk-neutral)"`

---

## Task 10: Engine facade + MCP tools (`search_docs`, `get_documentation`)

**Files:**
- Modify: `src/uci/engine.py` (new `docs_overview()`, `search_docs()`, `get_documentation()`, `capabilities()`)
- Modify: `src/uci/mcp/tools.py` (2 specs + dispatch)
- Test: `tests/test_mcp.py` (append), `tests/test_docs_surfaces.py` (append)

- [ ] **Step 10.1: Failing tests.** Append to `tests/test_mcp.py`:

```python
def test_docs_tools_listed_and_dispatch(tmp_path):
    eng = _doc_repo_engine(tmp_path)
    from uci.mcp.tools import dispatch, list_tools
    names = {t["name"] for t in list_tools(eng)}
    assert {"search_docs", "get_documentation"} <= names
    res = dispatch(eng, "get_documentation", {"symbol": "COSGN00C"})
    assert res["documentation"] and res["documentation"][0]["path"] == "README.md"
    res = dispatch(eng, "search_docs", {"query": "signon"})
    assert res["hits"] and all(h["kind"] == "doc_section" for h in res["hits"])
```

- [ ] **Step 10.2:** Run — FAIL.
- [ ] **Step 10.3: Implement.** Engine methods (near `find_tests_for_symbol` for style):

```python
    def search_docs(self, query: str, top_k: int = 10) -> dict:
        """Documentation-only search: DOC_SECTION hits with excerpts."""
        return self.search(query, top_k=top_k, kinds=[EntityType.DOC_SECTION])

    def get_documentation(self, symbol: str) -> dict:
        """Doc sections that describe *symbol* (or its file/module), best-confidence first."""
        found = self.find_symbol(symbol)
        matches = found.get("matches", [])
        if not matches:
            return {**found, "documentation": []}
        entity = self.graph.get_entity(matches[0]["entity_id"])
        docs = self._impact()._documentation(entity)
        for d in docs:                       # attach an excerpt for agent consumption
            chunk_texts = [c["text"] for c in self.metadata.iter_chunks(self.repo_id)
                           if c.get("entity_id") == d["entity_id"]]
            d["excerpt"] = (chunk_texts[0][:600] if chunk_texts else "")
        return {"symbol": symbol, "documentation": docs, "index": self._index_status()}

    def docs_overview(self) -> dict:
        """All doc files with their sections, link counts, and coverage of key artifacts."""
        sections = list(self.graph.entities(kind=EntityType.DOC_SECTION, repo_id=self.repo_id))
        by_path: dict[str, list] = {}
        linked: set[str] = set()
        for sec in sections:
            by_path.setdefault(sec.provenance.path, []).append(sec)
            for rel in self.graph.out_relationships(sec.id, [RelationType.DESCRIBES]):
                linked.add(rel.dst_id)
        key_kinds = (EntityType.LEGACY_PROGRAM, EntityType.JCL_JOB, EntityType.TRANSACTION_CODE)
        undocumented = []
        total_key = 0
        for kind in key_kinds:
            for ent in self.graph.entities(kind=kind, repo_id=self.repo_id):
                if ent.attributes.get("missing") or ent.attributes.get("external"):
                    continue
                total_key += 1
                if ent.id not in linked:
                    undocumented.append({"entity_id": ent.id, "name": ent.name,
                                         "kind": ent.kind.value, "path": ent.provenance.path})
        documents = [{
            "path": path,
            "sections": len(secs),
            "links": sum(len(self.graph.out_relationships(s.id, [RelationType.DESCRIBES]))
                         for s in secs),
        } for path, secs in sorted(by_path.items())]
        covered = total_key - len(undocumented)
        return {"documents": documents,
                "coverage": {"described": covered, "total": total_key,
                             "pct": round(100.0 * covered / total_key, 1) if total_key else 0.0},
                "undocumented": sorted(undocumented, key=lambda d: d["name"])[:200],
                "index": self._index_status()}
```

Add to `capabilities()`: `"search_docs"` / `"get_documentation"` available iff the graph has ≥1 `DOC_SECTION` (mirror how other capability flags are computed). MCP `TOOL_SPECS` additions:

```python
    {
        "name": "search_docs",
        "description": "Search ingested documentation (READMEs, specs, guides). Returns doc "
                       "sections with path+line provenance — pair with get_documentation for "
                       "the sections describing a specific symbol.",
        "inputSchema": {"type": "object", "required": ["query"],
                        "properties": {"query": {"type": "string"},
                                       "top_k": {"type": "integer", "default": 10}}},
    },
    {
        "name": "get_documentation",
        "description": "Documentation sections that describe a symbol (program, job, table, "
                       "function), with confidence-labeled links and excerpts. The design/spec "
                       "context a safe change needs.",
        "inputSchema": {"type": "object", "required": ["symbol"],
                        "properties": {"symbol": {"type": "string"}}},
    },
```

and in `dispatch`: route `search_docs` → `engine.search_docs(args["query"], top_k=int(args.get("top_k", 10)))`, `get_documentation` → `engine.get_documentation(args["symbol"])`.

- [ ] **Step 10.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_mcp.py tests/test_docs_surfaces.py -q` — PASS.
- [ ] **Step 10.5:** Commit: `git commit -am "feat(mcp): search_docs + get_documentation tools; docs_overview engine facade"`

---

## Task 11: CLI — `uci docs`

**Files:**
- Modify: `src/uci/cli/main.py`
- Test: `tests/test_cli.py` (append)

- [ ] **Step 11.1: Failing test** (follow the existing CLI-test invocation pattern in `tests/test_cli.py` — capsys + `main([...])`):

```python
def test_cli_docs_coverage(doc_sample_repo, capsys):
    main(["docs", "--path", str(doc_sample_repo)])
    out = capsys.readouterr().out
    assert "coverage" in out.lower() and "README.md" in out
```

- [ ] **Step 11.2:** Run — FAIL (unknown command).
- [ ] **Step 11.3: Implement** a `docs` subcommand mirroring the `gaps` subcommand's structure (argparse subparser, `--path`, `--json`): human output prints the document table (path · sections · links), the coverage line (`described/total (pct%)`), and the first 20 undocumented artifacts; `--json` dumps `engine.docs_overview()`.
- [ ] **Step 11.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_cli.py -q` — PASS.
- [ ] **Step 11.5:** Commit: `git commit -am "feat(cli): uci docs — documents, links, coverage, undocumented list"`

---

## Task 12: Dashboard — `/docs` page + API endpoints + nav

**Files:**
- Modify: `src/uci/api/server.py` (routes `/docs`, `/api/docs`, `/api/doc` detail)
- Modify: `src/uci/api/views.py` (nav entry + two render functions)
- Test: `tests/test_nav.py` (append), `tests/test_docs_surfaces.py` (append)

- [ ] **Step 12.1: Failing tests.** Append to `tests/test_nav.py` (match its existing route-smoke pattern):

```python
def test_docs_page_and_api(doc_dashboard_client):
    assert "/docs" in doc_dashboard_client.get("/docs")          # page renders
    body = doc_dashboard_client.get_json("/api/docs")
    assert body["coverage"]["total"] >= 1
    assert body["documents"][0]["path"] == "README.md"
```

(Reuse however `tests/test_nav.py`/`test_db_browser.py` spin up the stdlib server or call views directly — copy the established fixture.)

- [ ] **Step 12.2:** Run — FAIL.
- [ ] **Step 12.3: Implement.**
  - `views.py`: add `("/docs", "Docs")` to the **Understand** nav group. Add `page_docs(payload)` rendering: coverage stat card, documents table (path, sections, links — path links to the doc detail), undocumented-artifacts table (name links to `/search?q=<name>`), and `page_doc_detail(payload)`: the doc's sections in order, each with heading, line range, its DESCRIBES links (target name → entity link, resolution badge, confidence) and section text (from chunks) with **mention names rendered as links** (simple `html.escape` then wrap known target names — no markdown rendering engine in v1; keep it dependency-free like every other view).
  - `server.py`: `GET /docs` → page (query param `?path=` switches to detail); `GET /api/docs` → `engine.docs_overview()`; `GET /api/doc?path=…` → sections + links + chunk texts for one document (add `engine.doc_detail(path)` — a thin assembly over `graph.entities(kind=DOC_SECTION)` filtered by path + `out_relationships`).
- [ ] **Step 12.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_nav.py tests/test_docs_surfaces.py -q` — PASS.
- [ ] **Step 12.5: Manual smoke** (superpowers:verification-before-completion — actually look at it):

```bash
PYTHONPATH=src python3 -m uci.cli.main index evals/demo-repos/aws-mainframe-modernization-carddemo
PYTHONPATH=src python3 -m uci.cli.main serve --path evals/demo-repos/aws-mainframe-modernization-carddemo
# open http://127.0.0.1:8765/docs — expect README.md listed, coverage stat, COSGN00C linked
```

- [ ] **Step 12.6:** Commit: `git commit -am "feat(dashboard): /docs page — documents, sections, links, coverage panel"`

---

## Task 13: Optional LLM pass — `doc_links`

**Files:**
- Create: `src/uci/enrich/doc_links.py`
- Modify: `src/uci/enrich/enricher.py` (register pass), `src/uci/cli/main.py` (`--pass doc_links` already flows through), `docs/llm-enrichment.md` (Task 15)
- Test: `tests/test_enrich.py` (append; use the suite's existing fake LLM client pattern)

Semantics: for each `DOC_SECTION` with **zero** resolved `DESCRIBES` edges, send section text + a bounded inventory of candidate names (programs, jobs, transactions, tables — max ~200), ask STRICT JSON `{"describes": [str]}`; validate every name against the index (drop hallucinations); write edges with `extractor="llm:<model>"`, `resolution="llm-suggested"`, `confidence=0.6` (outside `RESOLVED_LEVELS` — candidates stratum, never impact). Cache by section content hash like other passes; count in `EnrichStats.doc_links`.

- [ ] **Step 13.1: Failing test** (append to `tests/test_enrich.py`, mirroring `_pass_capabilities` tests — fake client returns a fixed JSON):

```python
def test_doc_links_pass_links_unlinked_sections(doc_engine_with_fake_llm):
    eng, fake = doc_engine_with_fake_llm
    fake.reply('{"describes": ["COSGN00C", "NOTAREAL1"]}')
    stats = eng.enrich(passes=["doc_links"])
    assert stats["doc_links"] == 1            # hallucinated NOTAREAL1 dropped
    edges = [r for r in eng.graph.relationships(RelationType.DESCRIBES)
             if r.attributes.get("resolution") == "llm-suggested"]
    assert len(edges) == 1 and edges[0].provenance.confidence == 0.6
```

- [ ] **Step 13.2:** Run — FAIL.
- [ ] **Step 13.3: Implement** `_SYS_DOC_LINKS` prompt + pass function in `doc_links.py`, register in `Enricher.run` (`if "doc_links" in passes: self.client.default_tag = "enrich:doc_links"; self._pass_doc_links(limit, force)` delegating to the new module), add `doc_links: int = 0` to `EnrichStats`. System prompt (house style — restraint-first):

```python
_SYS_DOC_LINKS = (
    "You are linking one documentation section to the code artifacts it describes. "
    "Reply with STRICT JSON only: {\"describes\": [str]}. Choose ONLY names from the provided "
    "inventory that this section is ABOUT — not names merely mentioned in passing. "
    "If the section describes no specific artifact, reply {\"describes\": []}. No markdown."
)
```

- [ ] **Step 13.4:** Run: `PYTHONPATH=src python3 -m pytest tests/test_enrich.py -q` — PASS.
- [ ] **Step 13.5:** Commit: `git commit -am "feat(enrich): optional doc_links LLM pass — validated, llm-suggested, impact-neutral"`

---

## Task 14: Eval — doc-linkage track

**Files:**
- Create: `evals/docs_eval.py`, `evals/datasets/doc_links/carddemo.json`
- Modify: `tests/test_evals_gate.py` (append gate), `evals/README.md` (row)
- Test: `tests/test_docs_eval.py` (create — smoke on the sample fixture)

- [ ] **Step 14.1: Build the golden dataset.** Hand-label CardDemo: open `evals/demo-repos/aws-mainframe-modernization-carddemo/README.md`, list every (section-heading → artifact) pair a careful engineer would assert (start from the grep in the design notes: `COSGN00C`, `COCRDUPC`, `COCRDLIC`, `COMBTRAN`, `CBPAUP0J`, `CC00`, `CT00–CT02`, …). Schema:

```json
{
  "name": "carddemo-doc-links",
  "version": 1,
  "repo": "evals/demo-repos/aws-mainframe-modernization-carddemo",
  "expected_links": [
    {"doc_path": "README.md", "section_contains": "Application Inventory",
     "target": "COSGN00C", "target_kind": "legacy_program"},
    {"doc_path": "README.md", "section_contains": "User Functions",
     "target": "CC00", "target_kind": "transaction_code"}
  ],
  "forbidden_links": [
    {"doc_path": "README.md", "target": "COBOL"},
    {"doc_path": "README.md", "target": "AWS"}
  ],
  "expected_gaps": []
}
```

Populate `expected_links` with **at least 15** verified pairs (verify each target actually exists in the repo index first: `uci query <name>`), `forbidden_links` with ≥5 plausible false positives, and any true documented-but-missing members you find into `expected_gaps`.

- [ ] **Step 14.2: Write the runner** (`evals/docs_eval.py`, pattern after `cfg_eval.py`'s structure: index repo via public `Engine`, compute, print JSON report + exit code): score = link **precision** (no forbidden/unexpected-wrong links) and **recall** (expected found), gate at `precision ≥ 0.9 and recall ≥ 0.8`; `--json` and `-v` flags; report includes every miss with the reason.
- [ ] **Step 14.3: Smoke test** (`tests/test_docs_eval.py`): run the scorer against the *sample fixture repo* with a 3-case inline dataset (fast; no demo-repo dependency in unit CI), assert precision/recall computed correctly for one hit, one miss, one forbidden.
- [ ] **Step 14.4:** Run the real track and iterate until the gate passes:

```bash
PYTHONPATH=src python3 evals/docs_eval.py -v
# expected final line: {"precision": ≥0.9, "recall": ≥0.8, "gate": "PASS"}
```

Tune only via the deterministic knobs (stoplist, member shape, kind-preference order) — never by special-casing a golden.

- [ ] **Step 14.5: Regression guard:** `PYTHONPATH=src python3 evals/run_eval.py --baseline evals/reports/baseline.json` — the existing `supported`/`mainframe` tracks must not regress (>1.0-pt track or >0.05 category per house rule). If retrieval scores moved because docs now rank, re-commit `baseline.json` **with the explanation in the commit message**.
- [ ] **Step 14.6:** Commit: `git commit -am "eval: doc-linkage track (precision/recall gate) + carddemo goldens"`

---

## Task 15: Documentation updates — REQUIRED action item

**Files (all Modify unless noted):** `README.md`, `docs/canonical-schema.md`, `docs/architecture.md`, `docs/retrieval-strategy.md`, `docs/mcp-tools.md`, `docs/dashboard.md`, `docs/llm-enrichment.md`, `docs/roadmap.md`, Create: `docs/documentation-ingestion.md`

This task is not optional polish — the repo's docs are contract documents (e.g. `run_eval.py`: "the doc wins and this file is fixed").

- [ ] **Step 15.1:** `docs/canonical-schema.md`: add `DOC_SECTION` to §2 (new "Documentation" group, tier `*`), `DESCRIBES` row to §3 (`DocSection → any` · "Documentation describes artifact; **never** dependency-like"), and a §8 note that documented-but-missing artifacts feed the gap registry with reason `documented-artifact-missing`.
- [ ] **Step 15.2:** Create `docs/documentation-ingestion.md` — the user guide: supported formats + the `docs` extra, the mention-resolution ladder table (copy from this plan's design section), configuration keys, gap semantics, `uci docs` usage, dashboard walkthrough, MCP tool contracts, the `doc_links` LLM pass, and **honesty notes** (docs never affect impact/risk/completeness; PDF line numbers refer to extracted text; `converted=true`).
- [ ] **Step 15.3:** `README.md`: extend "Parsed today" with documentation formats; add a short "Documentation in the graph" subsection under the canonical-graph section (3-5 lines + link to the guide); add `pip install -e ".[docs]"` to the install block; add the two MCP tools to the tools list.
- [ ] **Step 15.4:** `docs/mcp-tools.md`: full contracts for `search_docs` / `get_documentation` (inputs, outputs, `available` gating). `docs/retrieval-strategy.md`: doc signal weighting + `UCI_WEIGHT_DOC`. `docs/dashboard.md`: `/docs` page. `docs/llm-enrichment.md`: `doc_links` pass §. `docs/architecture.md`: add doc flow to the pipeline diagram (`ingest → parser(+docs) → …` and the converter box). `docs/roadmap.md`: mark documentation ingestion ✅ with a pointer.
- [ ] **Step 15.5:** Verify every code reference in the new/changed docs against the implementation (grep each mentioned symbol/env var — no doc may mention a name that doesn't exist).
- [ ] **Step 15.6:** Commit: `git commit -am "docs: documentation-ingestion guide + schema/README/mcp/dashboard/retrieval updates"`

---

## Task 16: Final verification gate

- [ ] **Step 16.1:** Full suite: `PYTHONPATH=src python3 -m pytest -q` — all pass, no skips beyond the pre-existing `optional_backend` set.
- [ ] **Step 16.2:** Both eval gates: `PYTHONPATH=src python3 evals/docs_eval.py` (PASS) and `PYTHONPATH=src python3 evals/run_eval.py --baseline evals/reports/baseline.json` (no regression, or explained re-baseline).
- [ ] **Step 16.3:** End-to-end smoke on a real repo (drive the actual flow, not just tests):

```bash
PYTHONPATH=src python3 -m uci.cli.main index evals/demo-repos/aws-mainframe-modernization-carddemo
PYTHONPATH=src python3 -m uci.cli.main docs --path evals/demo-repos/aws-mainframe-modernization-carddemo
PYTHONPATH=src python3 -m uci.cli.main query "how do users sign on" --path evals/demo-repos/aws-mainframe-modernization-carddemo
PYTHONPATH=src python3 -m uci.cli.main impact COSGN00C --path evals/demo-repos/aws-mainframe-modernization-carddemo
```

Expected: `docs` shows README.md + coverage; `query` returns a mix with ≥1 `doc_section` hit labeled "Documentation…"; `impact` shows the `documentation` stratum; risk identical to pre-branch value for the same symbol.

- [ ] **Step 16.4:** Lint + types: `ruff check src tests && mypy src` (match repo config) — clean.
- [ ] **Step 16.5:** Use superpowers:requesting-code-review, then superpowers:finishing-a-development-branch (merge/PR decision belongs to the human).

---

## Non-goals (v1) — do not build

- Live connectors (Confluence/SharePoint APIs) and out-of-repo doc drop-folders.
- Markdown → HTML rendering engines in the dashboard (dependency-free escaped text + links only).
- `PARAGRAPH`-level doc links, image/diagram OCR, doc versioning across branches.
- Doc-drift detection (doc older than described code's churn) — noted as a natural follow-up; the data (git churn + DESCRIBES) is already in place.
- Modern-code *bare prose* mention linking (CamelCase words without backticks) — code-span/path/heading only.

## Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Doc noise floods retrieval | `UCI_WEIGHT_DOC` multiplier + `weight_doc=0` kill-switch + kinds filter |
| False links poison trust | unique-match-only ladder, stoplist, stub exclusion, forbidden-links eval cases |
| Gap registry noise | gaps only from code-span member-shaped misses |
| PDF extraction variance | converter failure → skip silently; page-anchored sections; `converted` provenance is honest |
| Ranking regressions | Task 14.5 baseline comparison is a hard gate |
