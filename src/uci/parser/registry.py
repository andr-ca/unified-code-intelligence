"""Parser registry: resolve a language to its structural parser. New languages register here."""

from __future__ import annotations

from .base import LanguageParser, ParseResult
from .bms_parser import BmsParser
from .cobol_parser import CobolParser
from .config_parser import ConfigParser
from .csd_parser import CsdParser
from .hlasm_parser import HlasmParser
from .javascript_parser import JavaScriptParser
from .jcl_parser import JclParser
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
register(CobolParser())
register(JclParser())
register(CsdParser())
register(HlasmParser())
register(BmsParser())


__all__ = ["register", "get_parser", "supported_languages", "ParseResult", "LanguageParser"]
