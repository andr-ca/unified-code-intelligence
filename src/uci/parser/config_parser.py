"""Lightweight configuration-key extractor.

Populates ``CONFIG_KEY`` entities from ``.env`` / ini / toml / yaml / json files so the graph can
answer "what configures this component?" without a full config parser. Best-effort and dependency-free.
"""

from __future__ import annotations

import json
import re

from ..core.entities import EntityType
from ..core.ids import qualify
from .base import LanguageParser, ParsedSymbol, ParseResult

_ENV = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_YAML_TOP = re.compile(r"^([A-Za-z_][\w-]*)\s*:")
_TOML_KEY = re.compile(r"^\s*([A-Za-z_][\w.-]*)\s*=")
_TOML_SECTION = re.compile(r"^\s*\[([^\]]+)\]")


class ConfigParser(LanguageParser):
    language = "config"
    extensions = (".env", ".ini", ".cfg", ".toml", ".yaml", ".yml", ".json", ".properties")

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        lower = path.lower()
        if lower.endswith(".json"):
            self._parse_json(source, module_qname, result)
        elif lower.endswith((".yaml", ".yml")):
            self._parse_lines(source, module_qname, result, _YAML_TOP)
        elif lower.endswith(".toml"):
            self._parse_toml(source, module_qname, result)
        else:  # .env, .ini, .cfg, .properties
            self._parse_lines(source, module_qname, result, _ENV)
        return result

    def _add(self, result, module_qname, key, line) -> None:
        result.symbols.append(ParsedSymbol(
            name=key, qualified_name=qualify(module_qname, key), kind=EntityType.CONFIG_KEY,
            start_line=line, end_line=line, parent_qname=module_qname,
            attributes={"config": True},
        ))

    def _parse_lines(self, source, module_qname, result, regex) -> None:
        for i, raw in enumerate(source.splitlines(), start=1):
            if raw.lstrip().startswith(("#", ";")):
                continue
            m = regex.match(raw)
            if m:
                self._add(result, module_qname, m.group(1), i)

    def _parse_toml(self, source, module_qname, result) -> None:
        section = ""
        for i, raw in enumerate(source.splitlines(), start=1):
            line = raw.strip()
            if line.startswith("#"):
                continue
            sec = _TOML_SECTION.match(line)
            if sec:
                section = sec.group(1)
                continue
            m = _TOML_KEY.match(line)
            if m:
                key = f"{section}.{m.group(1)}" if section else m.group(1)
                self._add(result, module_qname, key, i)

    def _parse_json(self, source, module_qname, result) -> None:
        try:
            data = json.loads(source)
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(data, dict):
            for key in data:
                self._add(result, module_qname, str(key), 1)


__all__ = ["ConfigParser"]
