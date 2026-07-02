"""Symbol-aware chunking with line-ownership (adapted from CodeRAG).

Each source line is owned by the *smallest* symbol span that contains it, so a method inside a class
is chunked as the method (not swallowed by the class). Unowned gaps are filled with sliding windows.
Oversized symbols are windowed within their own span. The result is non-overlapping chunks aligned to
real code units, each linkable back to a graph entity.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from ..core.entities import EntityType
from ..core.normalize import tokenize
from ..parser.base import ParsedSymbol

_CHUNKABLE = frozenset(
    {
        EntityType.FUNCTION,
        EntityType.METHOD,
        EntityType.CLASS,
        EntityType.INTERFACE,
        EntityType.TEST,
        EntityType.LEGACY_PROGRAM,
        EntityType.COPYBOOK,
        EntityType.JCL_JOB,
    }
)

# Best-effort secret patterns masked before any text is stored/embedded (recommendations §5.2).
_SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [^-]+-----"),
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key)"
        r"(\s*[:=]\s*)(['\"]?)([^\s'\"]{8,})\3"
    ),
]
_REDACTED = "***REDACTED***"


def scrub_secrets(text: str) -> str:
    """Mask obvious secrets (AWS keys, PEM blocks, key=value credential literals). Best-effort."""
    def _mask_kv(m: re.Match) -> str:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}{_REDACTED}{m.group(3)}"

    out = _SECRET_PATTERNS[0].sub(_REDACTED, text)
    out = _SECRET_PATTERNS[1].sub(_REDACTED, out)
    out = _SECRET_PATTERNS[2].sub(_mask_kv, out)
    return out


@dataclass
class Chunk:
    id: str
    repo_id: str
    path: str
    symbol: str
    kind: str
    language: str
    start_line: int
    end_line: int
    text: str
    entity_id: str = ""
    tokens: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "repo_id": self.repo_id, "path": self.path, "symbol": self.symbol,
            "kind": self.kind, "language": self.language, "start_line": self.start_line,
            "end_line": self.end_line, "text": self.text, "entity_id": self.entity_id,
            "tokens": self.tokens,
        }


def _windows(start: int, end: int, size: int, overlap: int) -> Iterator[tuple[int, int]]:
    step = max(1, size - overlap)
    s = start
    while s <= end:
        e = min(end, s + size - 1)
        yield s, e
        if e >= end:
            break
        s += step


def build_chunks(
    *,
    repo_id: str,
    path: str,
    language: str,
    source: str,
    symbols: Sequence[ParsedSymbol],
    entity_id_for: Callable[[ParsedSymbol], str],
    max_chunk_lines: int = 200,
    window_lines: int = 60,
    window_overlap: int = 10,
) -> list[Chunk]:
    # Never chunk/embed config-language files: index their KEYS as entities, keep values out of the
    # store, out of vectors, and out of any cloud embedding API (recommendations §5.1).
    if language == "config":
        return []
    lines = source.split("\n")
    n = len(lines)
    if n == 0:
        return []

    owner: list[ParsedSymbol | None] = [None] * (n + 1)
    for sym in symbols:
        if sym.kind not in _CHUNKABLE or sym.end_line < sym.start_line:
            continue
        span = sym.end_line - sym.start_line
        for line in range(max(1, sym.start_line), min(n, sym.end_line) + 1):
            cur = owner[line]
            if cur is None or (cur.end_line - cur.start_line) > span:
                owner[line] = sym

    chunks: list[Chunk] = []
    line = 1
    while line <= n:
        current = owner[line]
        run_end = line
        while run_end + 1 <= n and owner[run_end + 1] is current:
            run_end += 1
        _emit_run(
            chunks, current, line, run_end, lines, repo_id, path, language,
            entity_id_for, max_chunk_lines, window_lines, window_overlap,
        )
        line = run_end + 1
    return chunks


def _emit_run(
    chunks, owner, start, end, lines, repo_id, path, language,
    entity_id_for, max_chunk_lines, window_lines, window_overlap,
) -> None:
    def text_of(a: int, b: int) -> str:
        return "\n".join(lines[a - 1:b])

    if owner is None:
        # gap: window it, skipping whitespace-only spans
        for a, b in _windows(start, end, window_lines, window_overlap):
            body = text_of(a, b)
            if not body.strip():
                continue
            chunks.append(_make_chunk(repo_id, path, "", "window", language, a, b, body, ""))
        return

    eid = entity_id_for(owner)
    length = end - start + 1
    if length <= max_chunk_lines:
        chunks.append(_make_chunk(
            repo_id, path, owner.qualified_name, owner.kind.value, language,
            start, end, text_of(start, end), eid,
        ))
    else:
        for a, b in _windows(start, end, max_chunk_lines, window_overlap):
            chunks.append(_make_chunk(
                repo_id, path, owner.qualified_name, owner.kind.value, language,
                a, b, text_of(a, b), eid,
            ))


def _make_chunk(repo_id, path, symbol, kind, language, start, end, text, entity_id) -> Chunk:
    text = scrub_secrets(text)
    toks = sorted(set(tokenize(text) + tokenize(symbol)))[:256]
    return Chunk(
        id=f"chunk:{repo_id}:{path}:{start}-{end}",
        repo_id=repo_id, path=path, symbol=symbol, kind=kind, language=language,
        start_line=start, end_line=end, text=text, entity_id=entity_id, tokens=toks,
    )


def embed_chunks(provider, chunks: Iterable[Chunk]) -> list[tuple[str, list[float], dict]]:
    """Embed a batch of chunks, returning ``(chunk_id, vector, metadata)`` upsert tuples."""
    chunk_list = list(chunks)
    if not chunk_list or not getattr(provider, "available", False):
        return []
    vectors = provider.embed_documents([c.text for c in chunk_list])
    items: list[tuple[str, list[float], dict]] = []
    for chunk, vector in zip(chunk_list, vectors):
        if not vector:
            continue
        items.append((chunk.id, vector, {"repo_id": chunk.repo_id, "model": provider.model_id}))
    return items


__all__ = ["Chunk", "build_chunks", "embed_chunks", "scrub_secrets"]
