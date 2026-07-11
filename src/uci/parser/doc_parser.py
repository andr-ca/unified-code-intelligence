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


def _slug(text: str) -> str:
    return _SLUG_STRIP.sub("-", text.lower()).strip("-")[:60] or "section"


def _section_at(sections: list[ParsedSymbol], line: int) -> ParsedSymbol | None:
    for sec in sections:
        if sec.start_line <= line <= sec.end_line:
            return sec
    return sections[0] if sections else None


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

    # -- mentions -----------------------------------------------------------
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


__all__ = ["DocParser"]
