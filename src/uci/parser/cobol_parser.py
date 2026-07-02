"""COBOL structural parser (dependency-free, fixed- and free-format tolerant).

Extracts the facts that are *deterministic by construction* in COBOL source
(see docs/lsp-refactoring-recommendations.md §3.1/§3.3):

  - ``PROGRAM-ID. NAME``            -> LEGACY_PROGRAM symbol
  - copybook members (``.cpy``)     -> COPYBOOK symbol; ``EXEC SQL DECLARE <t> TABLE`` -> MAPS_TO
  - ``COPY MEMBER`` / ``EXEC SQL INCLUDE MEMBER`` -> imports (copybook dependency)
  - ``CALL 'PROG'``                 -> static call (resolvable)
  - ``CALL WS-NAME``                -> dynamic call site — unless a unique literal reaches the
                                       variable (``MOVE 'PROG' TO WS-NAME`` / ``VALUE 'PROG'``),
                                       which recovers the target at the *inferred* (R2) rung
  - ``EXEC CICS XCTL/LINK PROGRAM('P'|var)`` -> static call / dynamic site (same dataflow rule)
  - ``EXEC CICS READ/WRITE/... FILE('F')``   -> READS/WRITES dataset links
  - ``EXEC CICS SEND/RECEIVE MAP('M')``      -> USES screen links
  - ``SELECT f ASSIGN TO dd`` + ``OPEN INPUT/OUTPUT/I-O/EXTEND`` -> READS/WRITES dataset links
  - ``EXEC SQL SELECT/INSERT/UPDATE/DELETE`` -> READS/WRITES table links
  - paragraph labels + ``PERFORM PARA``      -> PARAGRAPH symbols and intra-program call links

Fixed-format continuation lines (``-`` in column 7) are joined before matching so literals
split across lines are not lost.
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
    # the variable form may be a subscripted table element: PROGRAM(MENU-PGM(WS-OPT)) — the
    # inner parens must not break the match (dropping it would hide real dynamic dispatch)
    r"\b(XCTL|LINK)\b[^.]*?\bPROGRAM\s*\(\s*(?:'([A-Z0-9$#@-]+)'|([A-Z0-9-]+(?:\s*\([^)]*\))?))\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_RE_CICS_FILE = re.compile(
    r"\b(READ|WRITE|REWRITE|DELETE|STARTBR|READNEXT|READPREV)\b[^.]*?"
    r"\b(?:FILE|DATASET)\s*\(\s*'([A-Z0-9$#@]+)'\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_RE_CICS_MAP = re.compile(
    r"\b(SEND|RECEIVE)\b[^.]*?\bMAP\s*\(\s*'([A-Z0-9$#@]+)'\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_RE_SQL_DECLARE = re.compile(
    r"\bDECLARE\s+([A-Z_][A-Z_0-9]*(?:\.[A-Z_][A-Z_0-9]*)?)\s+TABLE", re.IGNORECASE
)
_RE_SQL_READ = re.compile(r"\bFROM\s+([A-Z_][A-Z_0-9]*(?:\.[A-Z_][A-Z_0-9]*)?)", re.IGNORECASE)
_RE_SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Z_][A-Z_0-9]*(?:\.[A-Z_][A-Z_0-9]*)?)",
    re.IGNORECASE,
)
_RE_MOVE_LITERAL = re.compile(r"\bMOVE\s+'([A-Z0-9$#@-]+)'\s+TO\s+([A-Z][A-Z0-9-]*)", re.IGNORECASE)
_RE_MOVE_IDENT = re.compile(r"\bMOVE\s+([A-Z][A-Z0-9-]*(?:\s*\([^)]*\))?)\s+TO\s+([A-Z][A-Z0-9-]*)", re.IGNORECASE)
_RE_VALUE_LITERAL = re.compile(r"\b([A-Z][A-Z0-9-]*)\s+PIC\b[^.]*\bVALUE\s+'([A-Z0-9$#@-]+)'", re.IGNORECASE)
_RE_SELECT_ASSIGN = re.compile(
    r"\bSELECT\s+(?:OPTIONAL\s+)?([A-Z][A-Z0-9-]*)\s+ASSIGN\s+TO\s+([A-Z0-9$#@-]+)", re.IGNORECASE
)
_RE_OPEN = re.compile(r"\bOPEN\s+(INPUT|OUTPUT|I-O|EXTEND)\s+([A-Z0-9-]+(?:[ ,]+[A-Z0-9-]+)*)", re.IGNORECASE)
_RE_PERFORM = re.compile(r"\bPERFORM\s+([A-Z][A-Z0-9-]*)(?:\s+(?:THRU|THROUGH)\s+([A-Z][A-Z0-9-]*))?", re.IGNORECASE)
_RE_PARAGRAPH = re.compile(r"^([A-Z0-9][A-Z0-9-]*)\s*\.\s*$", re.IGNORECASE)

#: COBOL verbs/keywords that can follow CALL/PERFORM patterns but are never targets.
_NOT_A_TARGET = frozenset({
    "FUNCTION", "USING", "RETURNING", "END-CALL", "UNTIL", "VARYING", "TIMES",
    "TEST", "WITH", "THRU", "THROUGH",
})
#: SQL identifiers that are not application tables.
_SQL_NOISE = frozenset({"SYSIBM", "SQLCA", "SQLDA", "DUAL"})
#: Paragraph-position words that are structure, not paragraphs.
_NOT_A_PARAGRAPH = frozenset({
    "PROCEDURE", "IDENTIFICATION", "ENVIRONMENT", "DATA", "DIVISION",
    "SECTION", "GOBACK", "STOP", "EXIT", "EJECT", "SKIP1", "SKIP2", "SKIP3",
})


def _code_lines(source: str):
    """Yield ``(lineno, code)`` skipping fixed-format comment lines, **joining continuation
    lines** (``-`` in column 7) onto their logical start, and trimming sequence columns."""
    pending: tuple[int, str] | None = None
    for i, raw in enumerate(source.splitlines(), start=1):
        if len(raw) >= 7 and raw[6] in ("*", "/"):
            continue
        stripped = raw.lstrip()
        if stripped.startswith("*"):
            continue
        if len(raw) >= 7 and raw[6] == "-":
            # continuation: append content (col 8+) to the pending logical line
            if pending is not None:
                pending = (pending[0], pending[1] + raw[7:72].lstrip().lstrip("'"))
            continue
        if pending is not None:
            yield pending
        if len(raw) > 72:
            code = raw[7:72]
        elif len(raw) >= 7 and raw[:6].strip().isdigit():
            code = raw[7:]
        else:
            code = raw
        pending = (i, code)
    if pending is not None:
        yield pending


class CobolParser(LanguageParser):
    language = "cobol"
    extensions = (".cbl", ".cob", ".cpy")

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        is_copybook = path.lower().endswith(".cpy")
        n_lines = max(1, source.count("\n") + 1)

        if is_copybook:
            declared = [m.group(1).upper() for m in _RE_SQL_DECLARE.finditer(source)]
            result.symbols.append(ParsedSymbol(
                name=module_qname, qualified_name=module_qname, kind=EntityType.COPYBOOK,
                start_line=1, end_line=n_lines, parent_qname=None,
                attributes={"member": module_qname,
                            **({"dclgen": True, "declares": declared} if declared else {})},
            ))
            for table in declared:
                result.links.append(ParsedLink(
                    relation="maps_to", src_qname=module_qname, target_name=table,
                    target_kind=EntityType.DATABASE_TABLE.value, start_line=1,
                ))
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
        state = _BodyState(owner)
        for lineno, code in _code_lines(source):
            up = code.upper()

            self._collect_copy_and_sql(code, up, lineno, owner, result, state, collect_calls)
            if state.in_sql:
                if "END-EXEC" in up:
                    state.in_sql = False
                continue
            if not collect_calls:
                continue

            if self._collect_cics(code, up, lineno, owner, result, state):
                continue
            self._collect_dataflow(code, lineno, state)
            self._collect_files(code, up, lineno, owner, result, state)
            self._collect_paragraphs_and_performs(code, up, lineno, owner, result, state)
            self._collect_calls(code, lineno, state)

        # resolve dynamic calls whose variable is reached by exactly one literal (R2 dataflow)
        for kind, name, lineno, receiver in state.raw_calls:
            if kind == "static":
                result.calls.append(ParsedCall(
                    callee_name=name, caller_qname=owner, start_line=lineno))
                continue
            literals = state.var_literals.get(name, set())
            if len(literals) == 1 and name not in state.tainted_vars:
                result.calls.append(ParsedCall(
                    callee_name=next(iter(literals)), caller_qname=owner, start_line=lineno,
                    via_dataflow=True))
            else:
                result.calls.append(ParsedCall(
                    callee_name=name, caller_qname=owner, start_line=lineno, dynamic=True))

    # -- collectors ----------------------------------------------------
    def _collect_copy_and_sql(self, code, up, lineno, owner, result, state, collect_calls) -> None:
        for m in _RE_COPY.finditer(code):
            member = m.group(1).upper()
            if member in ("OF", "IN", "REPLACING") or member in state.seen_imports:
                continue
            state.seen_imports.add(member)
            result.imports.append(ParsedImport(module=member, start_line=lineno, raw=f"COPY {member}"))
        if "EXEC SQL" in up:
            state.in_sql = True
        if not state.in_sql:
            return
        for m in _RE_SQL_INCLUDE.finditer(code):
            member = m.group(1).upper()
            if member not in state.seen_imports:
                state.seen_imports.add(member)
                result.imports.append(ParsedImport(
                    module=member, start_line=lineno, raw=f"EXEC SQL INCLUDE {member}",
                    external=member in _SQL_NOISE))
        if collect_calls:
            for regex, relation in ((_RE_SQL_WRITE, "writes"), (_RE_SQL_READ, "reads")):
                for m in regex.finditer(code):
                    table = m.group(1).upper()
                    if table.split(".")[0] in _SQL_NOISE:
                        continue
                    if state.add_data((relation, "table", table)):
                        result.links.append(ParsedLink(
                            relation=relation, src_qname=owner, target_name=table,
                            target_kind=EntityType.DATABASE_TABLE.value, start_line=lineno))

    def _collect_cics(self, code, up, lineno, owner, result, state) -> bool:
        """Buffer EXEC CICS blocks (they span lines); returns True while consuming one."""
        if state.cics_buf is None and "EXEC CICS" in up:
            state.cics_buf = [code]
            state.cics_line = lineno
            if "END-EXEC" not in up:
                return True
        if state.cics_buf is None:
            return False
        if code not in state.cics_buf:
            state.cics_buf.append(code)
        if "END-EXEC" not in up:
            return True
        block = " ".join(state.cics_buf)
        state.cics_buf = None
        for m in _RE_CICS_PGM.finditer(block):
            literal, var = m.group(2), m.group(3)
            if literal:
                state.add_call("static", literal.upper(), state.cics_line)
            elif var:
                base = var.split("(")[0].strip().upper()
                if base in _NOT_A_TARGET:
                    continue
                if "(" in var:
                    # subscripted table element: value is data-driven by definition —
                    # taint the base so no literal-dataflow collapse can ever resolve it
                    state.tainted_vars.add(base)
                state.add_call("dynamic", base, state.cics_line)
        for m in _RE_CICS_FILE.finditer(block):
            verb, fname = m.group(1).upper(), m.group(2).upper()
            relation = "reads" if verb in ("READ", "STARTBR", "READNEXT", "READPREV") else "writes"
            if state.add_data((relation, "dataset", fname)):
                result.links.append(ParsedLink(
                    relation=relation, src_qname=owner, target_name=fname,
                    target_kind=EntityType.DATASET.value, start_line=state.cics_line,
                    attributes={"via": "cics"}))
        for m in _RE_CICS_MAP.finditer(block):
            verb, mname = m.group(1).upper(), m.group(2).upper()
            if state.add_data(("uses", "screen", mname)):
                result.links.append(ParsedLink(
                    relation="uses", src_qname=owner, target_name=mname,
                    target_kind=EntityType.SCREEN.value, start_line=state.cics_line,
                    attributes={"op": verb.lower()}))
        return True

    def _collect_dataflow(self, code, lineno, state) -> None:
        for m in _RE_MOVE_LITERAL.finditer(code):
            state.var_literals.setdefault(m.group(2).upper(), set()).add(m.group(1).upper())
        # a non-literal MOVE (identifier/table element) taints the variable: its value is no
        # longer provably a single literal, so dynamic calls through it must stay unresolved
        # (the menu-router pattern: one literal sign-off path + table-driven dispatch)
        for m in _RE_MOVE_IDENT.finditer(code):
            state.tainted_vars.add(m.group(2).upper())
        for m in _RE_VALUE_LITERAL.finditer(code):
            state.var_literals.setdefault(m.group(1).upper(), set()).add(m.group(2).upper())

    def _collect_files(self, code, up, lineno, owner, result, state) -> None:
        for m in _RE_SELECT_ASSIGN.finditer(code):
            state.select_map[m.group(1).upper()] = m.group(2).upper()
        m = _RE_OPEN.search(code)
        if m:
            mode = m.group(1).upper()
            relations = {"INPUT": ["reads"], "OUTPUT": ["writes"], "EXTEND": ["writes"],
                         "I-O": ["reads", "writes"]}[mode]
            for sel in re.split(r"[ ,]+", m.group(2).upper()):
                dataset = state.select_map.get(sel)
                if not dataset:
                    continue
                for relation in relations:
                    if state.add_data((relation, "dataset", dataset)):
                        result.links.append(ParsedLink(
                            relation=relation, src_qname=owner, target_name=dataset,
                            target_kind=EntityType.DATASET.value, start_line=lineno,
                            attributes={"via": "open", "select": sel}))

    def _collect_paragraphs_and_performs(self, code, up, lineno, owner, result, state) -> None:
        if "PROCEDURE DIVISION" in up:
            state.in_procedure = True
            return
        if not state.in_procedure:
            return
        stripped = code.strip()
        m = _RE_PARAGRAPH.match(stripped)
        # Area A: cols 8-11 -> indent 7-10 on raw fixed-format lines, 0-3 on trimmed ones
        indent = len(code) - len(code.lstrip())
        if m and indent <= 10 and m.group(1).upper() not in _NOT_A_PARAGRAPH:
            para = m.group(1).upper()
            state.current_paragraph = para
            result.symbols.append(ParsedSymbol(
                name=para, qualified_name=f"{owner}.{para}", kind=EntityType.PARAGRAPH,
                start_line=lineno, end_line=lineno, parent_qname=owner,
            ))
            return
        for m in _RE_PERFORM.finditer(code):
            for target in (m.group(1), m.group(2)):
                if not target or target.upper() in _NOT_A_TARGET:
                    continue
                src = f"{owner}.{state.current_paragraph}" if state.current_paragraph else owner
                result.links.append(ParsedLink(
                    relation="performs", src_qname=src, target_name=target.upper(),
                    target_kind=EntityType.PARAGRAPH.value, start_line=lineno,
                    attributes={"program": owner}))

    def _collect_calls(self, code, lineno, state) -> None:
        for m in _RE_CALL_LITERAL.finditer(code):
            state.add_call("static", m.group(1).upper(), lineno)
        for m in _RE_CALL_DYNAMIC.finditer(code):
            var = m.group(1).upper()
            if var not in _NOT_A_TARGET:
                state.add_call("dynamic", var, lineno)


class _BodyState:
    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.in_sql = False
        self.in_procedure = False
        self.cics_buf: list[str] | None = None
        self.cics_line = 0
        self.current_paragraph: str | None = None
        self.seen_imports: set[str] = set()
        self.seen_calls: set[tuple[str, str]] = set()
        self.seen_data: set[tuple] = set()
        self.raw_calls: list[tuple[str, str, int, str | None]] = []
        self.var_literals: dict[str, set[str]] = {}
        self.tainted_vars: set[str] = set()
        self.select_map: dict[str, str] = {}

    def add_call(self, kind: str, name: str, lineno: int) -> None:
        if kind == "dynamic" and ("static", name) in self.seen_calls:
            return
        if (kind, name) in self.seen_calls:
            return
        self.seen_calls.add((kind, name))
        self.raw_calls.append((kind, name, lineno, None))

    def add_data(self, key: tuple) -> bool:
        if key in self.seen_data:
            return False
        self.seen_data.add(key)
        return True


__all__ = ["CobolParser"]
