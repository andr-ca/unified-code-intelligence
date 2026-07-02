"""BMS (Basic Mapping Support) map parser — CICS screen definitions.

``NAME DFHMSD`` defines a mapset, ``NAME DFHMDI`` defines a map (screen) within it.
Programs reference them via ``EXEC CICS SEND/RECEIVE MAP('name') MAPSET('set')``, which the
COBOL parser emits as ``uses`` links — this parser supplies the SCREEN entities those links
land on. Field-level DFHMDF extraction is out of scope for now.
"""

from __future__ import annotations

import re

from ..core.entities import EntityType
from .base import LanguageParser, ParsedSymbol, ParseResult

_RE_MACRO = re.compile(r"^([A-Z0-9$#@]+)\s+(DFHMSD|DFHMDI)\b", re.IGNORECASE)


class BmsParser(LanguageParser):
    language = "bms"
    extensions = (".bms",)

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        mapset = module_qname
        maps: list[tuple[str, int]] = []
        for i, raw in enumerate(source.splitlines(), start=1):
            if raw.startswith("*"):
                continue
            m = _RE_MACRO.match(raw[:72])
            if not m:
                continue
            name, macro = m.group(1).upper(), m.group(2).upper()
            if macro == "DFHMSD" and name != "END":
                mapset = name
                result.symbols.append(ParsedSymbol(
                    name=name, qualified_name=module_qname, kind=EntityType.SCREEN,
                    start_line=i, end_line=i,
                    attributes={"bms": "mapset", "file_module": module_qname},
                ))
            elif macro == "DFHMDI":
                maps.append((name, i))
        for name, line in maps:
            result.symbols.append(ParsedSymbol(
                name=name, qualified_name=name, kind=EntityType.SCREEN,
                start_line=line, end_line=line, parent_qname=mapset,
                attributes={"bms": "map", "mapset": mapset, "file_module": module_qname},
            ))
        return result


__all__ = ["BmsParser"]
