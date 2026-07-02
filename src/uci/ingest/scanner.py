"""Repository scanner: walk the tree, prune ignored dirs, yield indexable files."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

from ..config import Config
from .ignore import IgnoreMatcher
from .langdetect import detect_language, is_text


@dataclass
class ScannedFile:
    rel_path: str
    abs_path: str
    size: int
    mtime: float
    language: str


def _rel(base: str, path: str) -> str:
    return os.path.relpath(path, base).replace("\\", "/")


def scan(config: Config) -> Iterator[ScannedFile]:
    root = str(config.repo_path)
    matcher = IgnoreMatcher(root, config.ignore_globs, config.use_gitignore)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = _rel(root, dirpath)
        if rel_dir == ".":
            rel_dir = ""
        # prune ignored directories in place so os.walk skips them entirely
        kept = []
        for d in dirnames:
            rp = f"{rel_dir}/{d}" if rel_dir else d
            if not matcher.is_ignored(rp):
                kept.append(d)
        dirnames[:] = kept

        for fn in filenames:
            rp = f"{rel_dir}/{fn}" if rel_dir else fn
            if matcher.is_ignored(rp):
                continue
            language = detect_language(rp)
            if language is None:
                if not (config.index_all_text and is_text(rp)):
                    continue
                language = "text"
            abs_path = os.path.join(dirpath, fn)
            try:
                st = os.stat(abs_path)
            except OSError:
                continue
            if st.st_size > config.max_file_bytes:
                continue
            yield ScannedFile(rp, abs_path, st.st_size, st.st_mtime, language)


__all__ = ["ScannedFile", "scan"]
