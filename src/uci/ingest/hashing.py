"""File hashing and content reading (content-hash change detection, adapted from CodeRAG)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_text(path: str | Path, max_bytes: int) -> str | None:
    """Read a text file, returning ``None`` for binary or oversized files."""
    p = Path(path)
    try:
        if p.stat().st_size > max_bytes:
            return None
        data = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:  # crude binary sniff
        return None
    return data.decode("utf-8", errors="replace")


def read_head(path: str | Path, max_bytes: int = 8192) -> str | None:
    """Read up to ``max_bytes`` from the start of a file for content sniffing.

    Returns ``None`` for binary files (a null byte in the leading bytes). Bounded so
    content-first language analysis stays cheap even on large files."""
    try:
        with Path(path).open("rb") as fh:
            data = fh.read(max_bytes)
    except OSError:
        return None
    if b"\x00" in data[:4096]:  # crude binary sniff
        return None
    return data.decode("utf-8", errors="replace")


def file_signature(path: str | Path) -> tuple[int, float]:
    st = Path(path).stat()
    return st.st_size, st.st_mtime


__all__ = ["hash_text", "hash_bytes", "read_text", "read_head", "file_signature"]
