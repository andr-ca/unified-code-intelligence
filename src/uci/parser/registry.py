"""Parser registry: resolve a language to its structural parser. New languages register here."""

from __future__ import annotations

from .base import LanguageParser, ParseResult
from .config_parser import ConfigParser
from .javascript_parser import JavaScriptParser
from .python_parser import PythonParser

_PARSERS: dict[str, LanguageParser] = {}


def register(parser: LanguageParser) -> None:
    _PARSERS[parser.language] = parser


def get_parser(language: str) -> LanguageParser | None:
    return _PARSERS.get(language)


def supported_languages() -> list[str]:
    return sorted(_PARSERS)


# register built-ins
register(PythonParser())
register(JavaScriptParser())
register(ConfigParser())


__all__ = ["register", "get_parser", "supported_languages", "ParseResult", "LanguageParser"]
