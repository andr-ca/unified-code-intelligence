"""CICS CSD parser — the system's own routing table (deterministic by construction).

``DEFINE TRANSACTION(T) ... PROGRAM(P)`` is exactly how CICS routes transaction T to
program P; extracting it yields R0 `INVOKES` edges (docs/lsp-refactoring-recommendations.md §3.6).
DEFINE PROGRAM/FILE/MAPSET entries are recorded as attributes-only for now.
"""

from __future__ import annotations

import re

from ..core.entities import EntityType
from .base import LanguageParser, ParsedLink, ParsedSymbol, ParseResult

_RE_DEFINE = re.compile(r"DEFINE\s+(TRANSACTION|PROGRAM|FILE|MAPSET)\s*\(\s*([A-Z0-9$#@]+)\s*\)", re.IGNORECASE)
_RE_PROGRAM_ATTR = re.compile(r"\bPROGRAM\s*\(\s*([A-Z0-9$#@-]+)\s*\)", re.IGNORECASE)
_RE_GROUP = re.compile(r"\bGROUP\s*\(\s*([A-Z0-9$#@]+)\s*\)", re.IGNORECASE)
_RE_DSNAME = re.compile(r"\bDSNAME\s*\(\s*([A-Z0-9$#@.]+)\s*\)", re.IGNORECASE)


class CsdParser(LanguageParser):
    language = "csd"
    extensions = (".csd",)

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        lines = source.splitlines()

        # split into DEFINE blocks (a block runs until the next DEFINE)
        blocks: list[tuple[int, str]] = []
        current: list[str] = []
        start = 1
        for i, raw in enumerate(lines, start=1):
            if raw.lstrip().startswith("*"):
                continue
            if re.match(r"\s*DEFINE\s", raw, re.IGNORECASE) and current:
                blocks.append((start, " ".join(current)))
                current = [raw]
                start = i
            else:
                if not current:
                    start = i
                current.append(raw)
        if current:
            blocks.append((start, " ".join(current)))

        for line, block in blocks:
            dm = _RE_DEFINE.search(block)
            if not dm:
                continue
            kind, name = dm.group(1).upper(), dm.group(2).upper()
            gm = _RE_GROUP.search(block)
            group = gm.group(1).upper() if gm else ""
            if kind == "TRANSACTION":
                result.symbols.append(ParsedSymbol(
                    name=name, qualified_name=name, kind=EntityType.TRANSACTION_CODE,
                    start_line=line, end_line=line,
                    attributes={"group": group, "file_module": module_qname},
                ))
                # PROGRAM(...) inside the transaction block, excluding the DEFINE name itself
                pm = _RE_PROGRAM_ATTR.search(block, dm.end())
                if pm:
                    result.links.append(ParsedLink(
                        relation="invokes", src_qname=name, target_name=pm.group(1).upper(),
                        target_kind=EntityType.LEGACY_PROGRAM.value, start_line=line,
                    ))
            elif kind == "MAPSET":
                result.symbols.append(ParsedSymbol(
                    name=name, qualified_name=name, kind=EntityType.SCREEN,
                    start_line=line, end_line=line,
                    attributes={"csd": "mapset", "group": group, "file_module": module_qname},
                ))
            elif kind == "FILE":
                # the CSD binds the logical CICS file name to its physical dataset — this is
                # what makes COBOL `READ FILE('ACCTDAT')` traceable to a real DSN
                dsn = _RE_DSNAME.search(block)
                result.symbols.append(ParsedSymbol(
                    name=name, qualified_name=name, kind=EntityType.DATASET,
                    start_line=line, end_line=line,
                    attributes={"csd": "file", "group": group,
                                "dsname": dsn.group(1).upper() if dsn else "",
                                "file_module": module_qname},
                ))
        return result


__all__ = ["CsdParser"]
