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


__all__ = ["DocParser"]
