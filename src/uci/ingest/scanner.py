"""Repository scanner: walk the tree, prune ignored dirs, yield indexable files."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass

from ..config import Config
from .hashing import read_head
from .ignore import IgnoreMatcher
from .langanalyze import analyze_language
from .langdetect import DOC_CONVERTER_LANGS, detect_language, is_doc, is_text


@dataclass
class ScannedFile:
    rel_path: str
    abs_path: str
    size: int
    mtime: float
    language: str


def _rel(base: str, path: str) -> str:
    return os.path.relpath(path, base).replace("\\", "/")


def _prune_dirs(dirnames: list[str], rel_dir: str, matcher: IgnoreMatcher) -> None:
    """Drop ignored subdirectories in place so ``os.walk`` never descends into them."""
    kept = []
    for d in dirnames:
        rp = f"{rel_dir}/{d}" if rel_dir else d
        if not matcher.is_ignored(rp):
            kept.append(d)
    dirnames[:] = kept


def _classify(config: Config, rel_dir: str, dirpath: str, fn: str,
              matcher: IgnoreMatcher) -> ScannedFile | None:
    """Vet one file and, if indexable, return it with its content-first language."""
    rp = f"{rel_dir}/{fn}" if rel_dir else fn
    if matcher.is_ignored(rp):
        return None
    abs_path = os.path.join(dirpath, fn)
    try:
        st = os.stat(abs_path)
    except OSError:
        return None
    # Converter formats (PDF/DOCX) are binary: classify by extension, use the doc size cap, and
    # skip when the doc pipeline is off or the converter library isn't installed.
    ext_lang = detect_language(rp)
    if ext_lang in DOC_CONVERTER_LANGS:
        if not config.index_docs or st.st_size > config.doc_max_bytes:
            return None
        try:
            from .docconvert import available
        except ImportError:
            return None
        if not available(ext_lang):
            return None
        return ScannedFile(rp, abs_path, st.st_size, st.st_mtime, ext_lang)
    if st.st_size > config.max_file_bytes:
        return None
    # content-first: classify by the file's head (the extension is only a tiebreaker), so
    # extensionless or mislabeled members — esp. mainframe PDS members with no suffix — are
    # still routed to the right parser.
    language = analyze_language(rp, read_head(abs_path))
    if language is None:
        if not (config.index_all_text and is_text(rp)):
            return None
        language = "text"
    if is_doc(language) and not config.index_docs:
        return None
    return ScannedFile(rp, abs_path, st.st_size, st.st_mtime, language)


def scan(config: Config) -> Iterator[ScannedFile]:
    root = str(config.repo_path)
    matcher = IgnoreMatcher(root, config.ignore_globs, config.use_gitignore)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = _rel(root, dirpath)
        if rel_dir == ".":
            rel_dir = ""
        _prune_dirs(dirnames, rel_dir, matcher)
        for fn in filenames:
            scanned = _classify(config, rel_dir, dirpath, fn, matcher)
            if scanned is not None:
                yield scanned


__all__ = ["ScannedFile", "scan"]
