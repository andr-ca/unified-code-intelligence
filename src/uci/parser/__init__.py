"""``uci.parser`` — Tree-sitter-ready parser abstraction and built-in language plugins."""

from __future__ import annotations

from .base import (
    LanguageParser,
    ParsedCall,
    ParsedImport,
    ParsedReference,
    ParsedSymbol,
    ParseResult,
)
from .registry import get_parser, register, supported_languages

__all__ = [
    "LanguageParser",
    "ParseResult",
    "ParsedSymbol",
    "ParsedImport",
    "ParsedCall",
    "ParsedReference",
    "get_parser",
    "register",
    "supported_languages",
]
