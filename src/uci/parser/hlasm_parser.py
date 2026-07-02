"""HLASM (High Level Assembler) linkage extractor.

Deliberately scoped to the *linkage-level* facts that are deterministic from raw source
(docs/lsp-refactoring-recommendations.md §3.4 — "extract external linkage yourself; it's
trivially deterministic"):

  - ``NAME CSECT|RSECT|START``  -> LEGACY_PROGRAM symbol (plus a member-stem symbol so flat
                                   member-name resolution always works: COBOL ``CALL 'COBDATFT'``
                                   must resolve to ``COBDATFT.asm`` whatever its CSECT is named)
  - ``ENTRY SYM``               -> exported entry points (attribute)
  - ``EXTRN/WXTRN SYM``         -> DEPENDS_ON link (external symbol the binder must resolve)
  - ``V(SYM)`` address constants and the ``CALL`` macro -> call edges (literal) /
    branch-to-register with no traceable V-con stays out (dynamic territory)
  - ``COPY MEMBER``             -> copybook dependency

Macro *expansion* and conditional assembly are explicitly out of scope — that precision tier
belongs to the Che4z HLASM LSP bridge (roadmap).
"""

from __future__ import annotations

import re

from ..core.entities import EntityType
from .base import LanguageParser, ParsedCall, ParsedImport, ParsedLink, ParsedSymbol, ParseResult

_RE_SECT = re.compile(r"^([A-Z$#@][A-Z0-9$#@]*)\s+(CSECT|RSECT|START)\b", re.IGNORECASE)
_RE_ENTRY = re.compile(r"^\s+ENTRY\s+([A-Z0-9$#@,\s]+)", re.IGNORECASE)
_RE_EXTRN = re.compile(r"^\s+(?:EXTRN|WXTRN)\s+([A-Z0-9$#@,\s]+)", re.IGNORECASE)
_RE_VCON = re.compile(r"=?V\(([A-Z$#@][A-Z0-9$#@]*)\)", re.IGNORECASE)
_RE_CALL_MACRO = re.compile(r"^(?:[A-Z$#@][A-Z0-9$#@]*)?\s+CALL\s+([A-Z$#@][A-Z0-9$#@]*)", re.IGNORECASE)
_RE_COPY = re.compile(r"^\s+COPY\s+([A-Z$#@][A-Z0-9$#@]*)", re.IGNORECASE)


def _asm_lines(source: str):
    """Yield (lineno, code) skipping comment lines (* in col 1, .* macro comments) and
    trimming the sequence/continuation columns (73-80)."""
    for i, raw in enumerate(source.splitlines(), start=1):
        if raw.startswith("*") or raw.lstrip().startswith(".*"):
            continue
        yield i, raw[:72]


class HlasmParser(LanguageParser):
    language = "hlasm"
    extensions = (".asm", ".hlasm")

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        n_lines = max(1, source.count("\n") + 1)

        csects: list[tuple[str, int]] = []
        entries: list[str] = []
        externs: list[tuple[str, int]] = []
        seen_calls: set[str] = set()
        seen_imports: set[str] = set()

        for lineno, code in _asm_lines(source):
            m = _RE_SECT.match(code)
            if m:
                csects.append((m.group(1).upper(), lineno))
                continue
            m = _RE_ENTRY.match(code)
            if m:
                entries.extend(s.strip().upper() for s in m.group(1).split(",") if s.strip())
                continue
            m = _RE_EXTRN.match(code)
            if m:
                for sym in (s.strip().upper() for s in m.group(1).split(",")):
                    if sym:
                        externs.append((sym, lineno))
                continue
            m = _RE_COPY.match(code)
            if m:
                member = m.group(1).upper()
                if member not in seen_imports:
                    seen_imports.add(member)
                    result.imports.append(ParsedImport(
                        module=member, start_line=lineno, raw=f"COPY {member}",
                    ))
                continue
            m = _RE_CALL_MACRO.match(code)
            if m:
                target = m.group(1).upper()
                if target not in seen_calls:
                    seen_calls.add(target)
                    result.calls.append(ParsedCall(
                        callee_name=target, caller_qname=module_qname, start_line=lineno,
                    ))
            for m in _RE_VCON.finditer(code):
                target = m.group(1).upper()
                if target not in seen_calls:
                    seen_calls.add(target)
                    result.calls.append(ParsedCall(
                        callee_name=target, caller_qname=module_qname, start_line=lineno,
                    ))

        # the member itself is the program (flat library convention) — CSECT names that
        # differ from the stem are recorded, and same-named CSECTs don't duplicate
        result.symbols.append(ParsedSymbol(
            name=module_qname, qualified_name=module_qname, kind=EntityType.LEGACY_PROGRAM,
            start_line=csects[0][1] if csects else 1, end_line=n_lines,
            attributes={"hlasm": True, "csects": [c for c, _ in csects], "entries": entries},
        ))
        for csect, line in csects:
            if csect != module_qname:
                result.symbols.append(ParsedSymbol(
                    name=csect, qualified_name=csect, kind=EntityType.LEGACY_PROGRAM,
                    start_line=line, end_line=n_lines, parent_qname=module_qname,
                    attributes={"hlasm": True, "csect_of": module_qname},
                ))
        for sym, line in externs:
            if sym not in seen_calls:  # V-con/CALL already models the stronger edge
                result.links.append(ParsedLink(
                    relation="depends_on", src_qname=module_qname, target_name=sym,
                    target_kind=EntityType.LEGACY_PROGRAM.value, start_line=line,
                ))
        return result


__all__ = ["HlasmParser"]
