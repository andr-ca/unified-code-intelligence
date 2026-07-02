"""COBOL structural parser (dependency-free, fixed- and free-format tolerant).

Extracts the facts that are *deterministic by construction* in COBOL source
(see docs/lsp-refactoring-recommendations.md §3.1/§3.3):

  - ``PROGRAM-ID. NAME``            -> LEGACY_PROGRAM symbol
  - copybook members (``.cpy``)     -> COPYBOOK symbol spanning the member
  - ``COPY MEMBER`` / ``EXEC SQL INCLUDE MEMBER`` -> imports (copybook dependency)
  - ``CALL 'PROG'``                 -> static call (resolvable)
  - ``CALL WS-NAME``                -> dynamic call site (never an edge — recorded
                                       as unresolved so completeness stays honest)
  - ``EXEC CICS XCTL/LINK PROGRAM('P'|var)`` -> static call / dynamic site
  - ``EXEC SQL SELECT/INSERT/UPDATE/DELETE`` -> READS/WRITES table links

Paragraph-level structure and BMS maps are deliberately out of scope for this pass.
"""

from __future__ import annotations

import re

from ..core.entities import EntityType
from .base import LanguageParser, ParsedCall, ParsedImport, ParsedLink, ParsedSymbol, ParseResult

_RE_PROGRAM_ID = re.compile(r"\bPROGRAM-ID\s*\.?\s+([A-Z0-9$#@-]+)", re.IGNORECASE)
_RE_CALL_LITERAL = re.compile(r"\bCALL\s+'([A-Z0-9$#@-]+)'", re.IGNORECASE)
_RE_CALL_DYNAMIC = re.compile(r"\bCALL\s+([A-Z][A-Z0-9-]*)\b(?!\s*')", re.IGNORECASE)
_RE_COPY = re.compile(r"\bCOPY\s+([A-Z0-9$#@-]+)\s*(?:\.|\s|$)", re.IGNORECASE)
_RE_SQL_INCLUDE = re.compile(r"\bINCLUDE\s+([A-Z0-9$#@-]+)", re.IGNORECASE)
_RE_CICS_PGM = re.compile(
    r"\b(XCTL|LINK)\b[^.]*?\bPROGRAM\s*\(\s*(?:'([A-Z0-9$#@-]+)'|([A-Z0-9-]+))\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_RE_SQL_READ = re.compile(r"\bFROM\s+([A-Z_][A-Z_0-9]*(?:\.[A-Z_][A-Z_0-9]*)?)", re.IGNORECASE)
_RE_SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Z_][A-Z_0-9]*(?:\.[A-Z_][A-Z_0-9]*)?)",
    re.IGNORECASE,
)

#: COBOL verbs that can follow CALL-like patterns but are never call targets.
_NOT_A_TARGET = frozenset({"FUNCTION", "USING", "RETURNING", "END-CALL"})
#: SQL identifiers that are not application tables.
_SQL_NOISE = frozenset({"SYSIBM", "SQLCA", "SQLDA", "DUAL"})


def _code_lines(source: str):
    """Yield ``(lineno, code)`` skipping fixed-format comment lines and sequence columns.

    Heuristic per line: if the line is long enough to be fixed-format and column 7 holds a
    comment/continuation indicator, honor it; otherwise treat the line as free-format.
    """
    for i, raw in enumerate(source.splitlines(), start=1):
        if len(raw) >= 7 and raw[6] in ("*", "/"):
            continue
        stripped = raw.lstrip()
        if stripped.startswith("*>") or stripped.startswith("*"):
            # free-format comment (or fixed comment on a short line)
            if not stripped.startswith("*>") and len(raw) >= 7 and raw[6] not in (" ", "-"):
                pass  # unlikely code; fall through conservatively
            continue
        code = raw[7:72] if len(raw) > 72 else (raw[7:] if len(raw) >= 7 and raw[:6].strip().isdigit() else raw)
        yield i, code


class CobolParser(LanguageParser):
    language = "cobol"
    extensions = (".cbl", ".cob", ".cpy")

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        is_copybook = path.lower().endswith(".cpy")
        n_lines = max(1, source.count("\n") + 1)

        if is_copybook:
            result.symbols.append(ParsedSymbol(
                name=module_qname, qualified_name=module_qname, kind=EntityType.COPYBOOK,
                start_line=1, end_line=n_lines, parent_qname=None,
                attributes={"member": module_qname},
            ))
            # copybooks can COPY other copybooks — still worth the dependency edges
            self._extract_body(source, module_qname, result, collect_calls=False)
            return result

        program = module_qname
        prog_line = 1
        for lineno, code in _code_lines(source):
            m = _RE_PROGRAM_ID.search(code)
            if m:
                program = m.group(1).upper().rstrip(".")
                prog_line = lineno
                break
        result.symbols.append(ParsedSymbol(
            name=program, qualified_name=program, kind=EntityType.LEGACY_PROGRAM,
            start_line=prog_line, end_line=n_lines, parent_qname=None,
            attributes={"program_id": program, "file_module": module_qname},
        ))
        self._extract_body(source, program, result, collect_calls=True)
        return result

    # ------------------------------------------------------------------
    def _extract_body(self, source: str, owner: str, result: ParseResult, collect_calls: bool) -> None:
        in_sql = False
        cics_buf: list[str] | None = None  # EXEC CICS spans lines; buffer until END-EXEC
        cics_line = 0
        seen_imports: set[str] = set()
        seen_calls: set[tuple] = set()
        seen_data: set[tuple] = set()

        def emit_call(target: str, lineno: int, dynamic: bool) -> None:
            key = ("dynamic" if dynamic else "static", target)
            if dynamic and ("static", target) in seen_calls:
                return
            if key in seen_calls:
                return
            seen_calls.add(key)
            result.calls.append(ParsedCall(
                callee_name=target, caller_qname=owner, start_line=lineno, dynamic=dynamic,
            ))

        for lineno, code in _code_lines(source):
            up = code.upper()

            if collect_calls and cics_buf is None and "EXEC CICS" in up:
                cics_buf = [code]
                cics_line = lineno
                if "END-EXEC" not in up:
                    continue
            if cics_buf is not None:
                if code not in cics_buf:
                    cics_buf.append(code)
                if "END-EXEC" in up:
                    block = " ".join(cics_buf)
                    for m in _RE_CICS_PGM.finditer(block):
                        literal, var = m.group(2), m.group(3)
                        if literal:
                            emit_call(literal.upper(), cics_line, dynamic=False)
                        elif var and var.upper() not in _NOT_A_TARGET:
                            emit_call(var.upper(), cics_line, dynamic=True)
                    cics_buf = None
                continue

            for m in _RE_COPY.finditer(code):
                member = m.group(1).upper()
                if member in ("OF", "IN", "REPLACING") or member in seen_imports:
                    continue
                seen_imports.add(member)
                result.imports.append(ParsedImport(
                    module=member, start_line=lineno, raw=f"COPY {member}",
                ))

            if "EXEC SQL" in up:
                in_sql = True
            if in_sql:
                for m in _RE_SQL_INCLUDE.finditer(code):
                    member = m.group(1).upper()
                    if member not in seen_imports:
                        seen_imports.add(member)
                        result.imports.append(ParsedImport(
                            module=member, start_line=lineno, raw=f"EXEC SQL INCLUDE {member}",
                            external=member in _SQL_NOISE,
                        ))
                if collect_calls:
                    for regex, relation in ((_RE_SQL_WRITE, "writes"), (_RE_SQL_READ, "reads")):
                        for m in regex.finditer(code):
                            table = m.group(1).upper()
                            if table.split(".")[0] in _SQL_NOISE:
                                continue
                            key = (relation, table)
                            if key in seen_data:
                                continue
                            seen_data.add(key)
                            result.links.append(ParsedLink(
                                relation=relation, src_qname=owner, target_name=table,
                                target_kind=EntityType.DATABASE_TABLE.value, start_line=lineno,
                            ))
                if "END-EXEC" in up:
                    in_sql = False
                continue  # SQL text is not COBOL call territory

            if not collect_calls:
                continue

            for m in _RE_CALL_LITERAL.finditer(code):
                emit_call(m.group(1).upper(), lineno, dynamic=False)
            for m in _RE_CALL_DYNAMIC.finditer(code):
                var = m.group(1).upper()
                if var not in _NOT_A_TARGET:
                    emit_call(var, lineno, dynamic=True)


__all__ = ["CobolParser"]
