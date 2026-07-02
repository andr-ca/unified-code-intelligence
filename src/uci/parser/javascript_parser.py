"""JavaScript / TypeScript structural parser.

The MVP uses a dependency-free scanner rather than Tree-sitter native grammars: it blanks the
contents of strings and comments (preserving offsets and line numbers), then extracts imports,
classes, interfaces, functions, methods, and calls with tolerant regexes + brace matching. A
Tree-sitter-backed parser can later be dropped in behind the same :class:`LanguageParser` interface.
"""

from __future__ import annotations

import bisect
import re

from ..core.entities import EntityType
from ..core.ids import qualify
from .base import (
    LanguageParser,
    ParsedCall,
    ParsedImport,
    ParsedReference,
    ParsedSymbol,
    ParseResult,
    resolve_js_module,
)

_KEYWORDS = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "function", "class", "await",
     "typeof", "new", "throw", "else", "do", "in", "of", "case", "yield", "super"}
)

_RE_FROM = re.compile(r"""(?:from|require\(\s*|import\s*\(\s*|import\s+)["']([^"']+)["']""")
_RE_IMPORT = re.compile(r"""import\s+(?P<clause>[^;'"]+?)\s+from\s+["'](?P<mod>[^"']+)["']""")
_RE_CLASS = re.compile(r"\bclass\s+(\w+)(?:\s+extends\s+([\w.]+))?(?:\s+implements\s+([\w.,\s]+))?")
_RE_INTERFACE = re.compile(r"\binterface\s+(\w+)(?:\s+extends\s+([\w.,\s]+))?")
_RE_FUNC = re.compile(r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(")
_RE_ARROW = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*[^=]+?)?=\s*"
    r"(?:async\s*)?(?:function\b|\([^;{]*?\)\s*(?::[^={]+)?=>|\w+\s*=>)"
)
_RE_METHOD = re.compile(
    r"^[ \t]*(?:public\s+|private\s+|protected\s+|static\s+|readonly\s+|async\s+|override\s+|get\s+|set\s+|\*\s*)*"
    r"(\w+)\s*(?:<[^>]*>)?\s*\([^;{}]*\)\s*(?::[^={;]+)?\{",
    re.MULTILINE,
)
_RE_CALL = re.compile(r"(?:(\w+)\s*\.\s*)?(\w+)\s*\(")


def _blank_strings_comments(source: str) -> str:
    """Replace string/comment contents with spaces, preserving newlines and total length."""
    out: list[str] = []
    i = 0
    n = len(source)
    state = "code"  # code | line | block | s | d | t
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                state = "line"; out.append("  "); i += 2; continue
            if ch == "/" and nxt == "*":
                state = "block"; out.append("  "); i += 2; continue
            if ch == "'":
                state = "s"; out.append(" "); i += 1; continue
            if ch == '"':
                state = "d"; out.append(" "); i += 1; continue
            if ch == "`":
                state = "t"; out.append(" "); i += 1; continue
            out.append(ch); i += 1; continue
        # inside string/comment
        if ch == "\n":
            out.append("\n"); i += 1
            if state == "line":
                state = "code"
            continue
        if state == "line":
            out.append(" "); i += 1; continue
        if state == "block":
            if ch == "*" and nxt == "/":
                state = "code"; out.append("  "); i += 2; continue
            out.append(" "); i += 1; continue
        if state in ("s", "d", "t"):
            if ch == "\\":
                out.append("  "); i += 2; continue
            if (state == "s" and ch == "'") or (state == "d" and ch == '"') or (state == "t" and ch == "`"):
                state = "code"; out.append(" "); i += 1; continue
            out.append(" "); i += 1; continue
    return "".join(out)


class _LineMap:
    def __init__(self, text: str) -> None:
        self.starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self.starts.append(i + 1)

    def line_of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


def _match_brace(scan: str, open_offset: int) -> int:
    """Return the offset just after the matching ``}`` for the ``{`` at *open_offset* (or len)."""
    depth = 0
    for i in range(open_offset, len(scan)):
        c = scan[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(scan)


class JavaScriptParser(LanguageParser):
    language = "javascript"
    extensions = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        scan = _blank_strings_comments(source)
        lines = _LineMap(scan)
        is_test = bool(re.search(r"\.(test|spec)\.|__tests__|/tests?/", path))

        # -- imports --------------------------------------------------------
        bound_lines: set[int] = set()
        for m in _RE_IMPORT.finditer(scan):
            spec = m.group("mod")
            line = lines.line_of(m.start())
            bound_lines.add(line)
            modq = resolve_js_module(module_qname, spec)
            binds = _parse_js_clause(m.group("clause"), modq) if modq else {}
            result.imports.append(ParsedImport(
                module=modq or spec, start_line=line, raw=m.group(0),
                external=modq is None, binds=binds,
            ))
        # side-effect / require / export-from specifiers not covered above
        for m in _RE_FROM.finditer(scan):
            line = lines.line_of(m.start())
            if line in bound_lines:
                continue
            spec = m.group(1)
            modq = resolve_js_module(module_qname, spec)
            result.imports.append(ParsedImport(
                module=modq or spec, start_line=line, raw=m.group(0), external=modq is None,
            ))

        symbols: list[ParsedSymbol] = []

        # -- classes & their methods ---------------------------------------
        for m in _RE_CLASS.finditer(scan):
            name = m.group(1)
            qname = qualify(module_qname, name)
            brace = scan.find("{", m.end())
            start = lines.line_of(m.start())
            end = lines.line_of(_match_brace(scan, brace) - 1) if brace != -1 else start
            bases = [m.group(2)] if m.group(2) else []
            interfaces = [s.strip() for s in (m.group(3) or "").split(",") if s.strip()]
            symbols.append(ParsedSymbol(
                name=name, qualified_name=qname, kind=EntityType.CLASS,
                start_line=start, end_line=end, parent_qname=module_qname,
                bases=bases + interfaces,
                attributes={"implements": interfaces},
            ))
            for b in bases + interfaces:
                result.references.append(ParsedReference(
                    name=b.split(".")[-1], from_qname=qname, start_line=start, kind="base"))
            if brace != -1:
                self._extract_methods(scan, lines, brace, _match_brace(scan, brace), qname, symbols)

        # -- interfaces -----------------------------------------------------
        for m in _RE_INTERFACE.finditer(scan):
            name = m.group(1)
            qname = qualify(module_qname, name)
            brace = scan.find("{", m.end())
            start = lines.line_of(m.start())
            end = lines.line_of(_match_brace(scan, brace) - 1) if brace != -1 else start
            symbols.append(ParsedSymbol(
                name=name, qualified_name=qname, kind=EntityType.INTERFACE,
                start_line=start, end_line=end, parent_qname=module_qname,
            ))

        # -- top-level functions & arrow functions -------------------------
        for regex in (_RE_FUNC, _RE_ARROW):
            for m in regex.finditer(scan):
                name = m.group(1)
                qname = qualify(module_qname, name)
                brace = scan.find("{", m.end() - 1)
                start = lines.line_of(m.start())
                end = lines.line_of(_match_brace(scan, brace) - 1) if brace != -1 else start
                kind = EntityType.TEST if (is_test and name.startswith("test")) else EntityType.FUNCTION
                symbols.append(ParsedSymbol(
                    name=name, qualified_name=qname, kind=kind,
                    start_line=start, end_line=end, parent_qname=module_qname,
                    is_exported="export" in scan[m.start():m.start() + 8],
                ))

        # de-duplicate symbols by qname keeping the widest range
        result.symbols = _dedupe_symbols(symbols)

        # -- calls: attribute by innermost containing symbol ---------------
        result.calls = self._extract_calls(scan, lines, result.symbols, module_qname)
        return result

    def _extract_methods(self, scan, lines, body_start, body_end, class_qname, symbols) -> None:
        segment = scan[body_start:body_end]
        for mm in _RE_METHOD.finditer(segment):
            name = mm.group(1)
            if name in _KEYWORDS:
                continue
            open_offset = body_start + mm.end() - 1  # position of the "{"
            start_line = lines.line_of(body_start + mm.start())
            end_line = lines.line_of(_match_brace(scan, open_offset) - 1)
            qname = qualify(class_qname, name)
            kind = EntityType.TEST if name in ("it", "test") else EntityType.METHOD
            symbols.append(ParsedSymbol(
                name=name, qualified_name=qname, kind=kind,
                start_line=start_line, end_line=max(start_line, end_line), parent_qname=class_qname,
            ))

    def _extract_calls(self, scan, lines, symbols, module_qname) -> list[ParsedCall]:
        # sort symbols by range width so innermost (narrowest) wins containment
        ranged = sorted(
            [s for s in symbols if s.end_line >= s.start_line],
            key=lambda s: (s.end_line - s.start_line),
        )
        calls: list[ParsedCall] = []
        for m in _RE_CALL.finditer(scan):
            receiver, callee = m.group(1), m.group(2)
            if callee in _KEYWORDS or not callee:
                continue
            line = lines.line_of(m.start())
            caller = module_qname
            for sym in ranged:
                if sym.kind in (EntityType.FUNCTION, EntityType.METHOD, EntityType.TEST) and sym.start_line <= line <= sym.end_line:
                    caller = sym.qualified_name
                    break
            calls.append(ParsedCall(callee_name=callee, caller_qname=caller,
                                    start_line=line, receiver=receiver))
        return calls


def _dedupe_symbols(symbols: list[ParsedSymbol]) -> list[ParsedSymbol]:
    best: dict[str, ParsedSymbol] = {}
    for sym in symbols:
        existing = best.get(sym.qualified_name)
        if existing is None or (sym.end_line - sym.start_line) > (existing.end_line - existing.start_line):
            best[sym.qualified_name] = sym
    return list(best.values())


def _parse_js_clause(clause: str, modq: str) -> dict[str, str]:
    """Parse an import clause into local_name -> target qname bindings.

    Handles ``Default``, ``{ a, b as c }``, and ``* as ns`` (and combinations).
    """
    binds: dict[str, str] = {}
    clause = clause.strip()
    brace = re.search(r"\{([^}]*)\}", clause)
    if brace:
        for item in brace.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            parts = re.split(r"\s+as\s+", item)
            original = parts[0].strip()
            local = parts[-1].strip()
            if original:
                binds[local] = f"{modq}.{original}"
        clause = clause[: brace.start()] + clause[brace.end():]
    ns = re.search(r"\*\s+as\s+(\w+)", clause)
    if ns:
        binds[ns.group(1)] = modq  # namespace import -> module
        clause = clause[: ns.start()] + clause[ns.end():]
    for tok in clause.split(","):
        tok = tok.strip()
        if re.fullmatch(r"\w+", tok):
            binds[tok] = modq  # default import -> module
    return binds


__all__ = ["JavaScriptParser"]
