"""Parser data model shared by all language plugins.

Parsers are deliberately *dumb structural extractors*: they emit language-agnostic
:class:`ParseResult` records (symbols, imports, calls, references) with line ranges. The indexer's
normalizer turns those into canonical entities/relationships and resolves calls/imports against the
symbol registry. This mirrors code-graph-rag's processor split but keeps resolution out of parsers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.entities import EntityType


@dataclass
class ParsedSymbol:
    name: str
    qualified_name: str
    kind: EntityType
    start_line: int
    end_line: int
    parent_qname: str | None = None
    signature: str = ""
    docstring: str = ""
    bases: list[str] = field(default_factory=list)      # base classes / implemented interfaces
    decorators: list[str] = field(default_factory=list)
    is_exported: bool = True
    attributes: dict = field(default_factory=dict)


@dataclass
class ParsedImport:
    module: str                       # resolved dotted module (best effort)
    names: list[str] = field(default_factory=list)
    alias: str | None = None
    start_line: int = 0
    raw: str = ""
    external: bool = False            # True if it looks like a third-party/stdlib import
    # local_name -> target qualified name (module or module.symbol). Enables precise, import-traced
    # call resolution ("from x import y as z" means z(...) resolves into x.y, not the global pool).
    binds: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedCall:
    callee_name: str                  # simple or last-segment name being called
    caller_qname: str                 # qualified name of the enclosing symbol (or module)
    start_line: int = 0
    receiver: str | None = None       # text of the object before the dot, if any
    receiver_type: str | None = None  # inferred class of the receiver (local var type inference)


@dataclass
class ParsedReference:
    name: str                         # referenced type/symbol name
    from_qname: str                   # qualified name of the referring symbol
    start_line: int = 0
    kind: str = "reference"           # "instantiation" | "base" | "reference"


@dataclass
class ParseResult:
    language: str
    module_qname: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    calls: list[ParsedCall] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class LanguageParser(ABC):
    """A structural extractor for one language family."""

    language: str = "unknown"
    extensions: tuple[str, ...] = ()

    @abstractmethod
    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        """Parse *source* into a :class:`ParseResult`. Must never raise on malformed input —
        record problems in ``ParseResult.errors`` and return what was extracted."""


def resolve_relative_module(module_qname: str, level: int, module: str) -> str:
    """Resolve a relative import (``from ..pkg import x``) to an absolute dotted module.

    *module_qname* is the current module (e.g. ``pricing.calculator``); *level* is the number of
    leading dots; *module* is the trailing module path (may be empty).
    """
    parts = module_qname.split(".")
    # a module file's package is everything but its own last segment
    package = parts[:-1] if parts else []
    # each extra dot beyond the first goes one package up
    if level > 1:
        package = package[: len(package) - (level - 1)] if len(package) >= (level - 1) else []
    base = ".".join(package)
    if module:
        return f"{base}.{module}" if base else module
    return base


def resolve_js_module(current_qname: str, specifier: str) -> str | None:
    """Resolve a JS/TS relative import specifier (``./mod``, ``../a/b``) to a dotted module qname.

    Returns ``None`` for bare (external) specifiers like ``react`` or ``@scope/pkg``.
    """
    if not specifier.startswith("."):
        return None
    parts = [p for p in specifier.split("/") if p not in ("", ".")]
    up = specifier.count("../")
    level = up + 1
    trailing = ".".join(p for p in parts if p != "..")
    resolved = resolve_relative_module(current_qname, level, trailing)
    # strip a trailing "index" (./mod/index -> mod)
    if resolved.endswith(".index"):
        resolved = resolved[: -len(".index")]
    return resolved or trailing


__all__ = [
    "ParsedSymbol",
    "ParsedImport",
    "ParsedCall",
    "ParsedReference",
    "ParseResult",
    "LanguageParser",
    "resolve_relative_module",
    "resolve_js_module",
]
