"""Ignore rules: default globs + ``.gitignore`` (best-effort, dependency-free)."""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path


class IgnoreMatcher:
    def __init__(self, root: str | Path, globs: Iterable[str], use_gitignore: bool = True) -> None:
        self.root = Path(root)
        self.patterns: list[str] = [g.strip("/") for g in globs if g.strip()]
        if use_gitignore:
            self.patterns.extend(self._load_gitignore())

    def _load_gitignore(self) -> list[str]:
        patterns: list[str] = []
        gi = self.root / ".gitignore"
        try:
            text = gi.read_text(encoding="utf-8")
        except OSError:
            return patterns
        for raw in text.splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                patterns.append(line.rstrip("/"))
        return patterns

    def is_ignored(self, rel_path: str) -> bool:
        rel_path = rel_path.replace("\\", "/").lstrip("/")
        if not rel_path:
            return False
        segments = rel_path.split("/")
        for pattern in self.patterns:
            if not pattern:
                continue
            if fnmatch(rel_path, pattern) or fnmatch(rel_path, f"{pattern}/*") or fnmatch(rel_path, f"*/{pattern}"):
                return True
            for seg in segments:
                if fnmatch(seg, pattern):
                    return True
        return False


__all__ = ["IgnoreMatcher"]
