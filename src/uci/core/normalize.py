"""Text/identifier normalization helpers shared across parsers and retrieval."""

from __future__ import annotations

import re

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_IDENTIFIER_HINT = re.compile(
    r"(`[^`]+`)|([A-Za-z_][A-Za-z0-9_]*\s*\()|([a-z]+_[a-z0-9_]+)|([a-z]+[A-Z][A-Za-z0-9]*)|(\w+\.\w+)"
)


def split_identifier(name: str) -> list[str]:
    """Split ``snake_case`` / ``camelCase`` / ``dotted.path`` identifiers into lowercase tokens."""
    if not name:
        return []
    parts: list[str] = []
    for chunk in _NON_ALNUM.split(name):
        if not chunk:
            continue
        for piece in _CAMEL_BOUNDARY.split(chunk):
            if piece:
                parts.append(piece.lower())
    return parts


def tokenize(text: str) -> list[str]:
    """Tokenize free text/code into lowercase identifier-aware tokens (for keyword search)."""
    tokens: list[str] = []
    for match in _TOKEN.findall(text or ""):
        tokens.append(match.lower())
        sub = split_identifier(match)
        if len(sub) > 1:
            tokens.extend(sub)
    return tokens


def looks_like_identifier(query: str) -> bool:
    """Heuristic used for adaptive fusion: does the query reference a code identifier?"""
    return bool(_IDENTIFIER_HINT.search(query or ""))


def simple_name(qualified_name: str) -> str:
    """Return the last dotted segment of a qualified name."""
    if not qualified_name:
        return ""
    return qualified_name.rstrip(".").split(".")[-1]


def sanitize_relative_path(path: str) -> str:
    """Normalize a repo-relative path to forward slashes without leading ``./``."""
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


__all__ = [
    "split_identifier",
    "tokenize",
    "looks_like_identifier",
    "simple_name",
    "sanitize_relative_path",
]
