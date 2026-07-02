"""Symbol-aware chunking tests: line ownership, non-overlap, window fallback."""

from __future__ import annotations

from uci.core.entities import EntityType
from uci.embeddings.chunking import build_chunks
from uci.parser.base import ParsedSymbol


def _chunks(source, symbols):
    return build_chunks(
        repo_id="r", path="a.py", language="python", source=source, symbols=symbols,
        entity_id_for=lambda s: f"e:{s.qualified_name}", max_chunk_lines=100,
        window_lines=5, window_overlap=1,
    )


def test_method_owned_by_smallest_span():
    source = "\n".join(f"line{i}" for i in range(1, 11))
    symbols = [
        ParsedSymbol("Foo", "m.Foo", EntityType.CLASS, 1, 10, parent_qname="m"),
        ParsedSymbol("bar", "m.Foo.bar", EntityType.METHOD, 3, 6, parent_qname="m.Foo"),
    ]
    chunks = _chunks(source, symbols)
    bar = [c for c in chunks if c.symbol == "m.Foo.bar"]
    assert len(bar) == 1
    assert (bar[0].start_line, bar[0].end_line) == (3, 6)
    assert bar[0].entity_id == "e:m.Foo.bar"


def test_chunks_do_not_overlap():
    source = "\n".join(f"line{i}" for i in range(1, 21))
    symbols = [
        ParsedSymbol("Foo", "m.Foo", EntityType.CLASS, 1, 20, parent_qname="m"),
        ParsedSymbol("a", "m.Foo.a", EntityType.METHOD, 3, 6, parent_qname="m.Foo"),
        ParsedSymbol("b", "m.Foo.b", EntityType.METHOD, 8, 12, parent_qname="m.Foo"),
    ]
    chunks = sorted(_chunks(source, symbols), key=lambda c: c.start_line)
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.end_line < nxt.start_line


def test_window_fallback_for_unowned_gaps():
    source = "\n".join(f"code{i}" for i in range(1, 13))
    symbols = [ParsedSymbol("f", "m.f", EntityType.FUNCTION, 5, 6, parent_qname="m")]
    chunks = _chunks(source, symbols)
    windows = [c for c in chunks if c.kind == "window"]
    assert windows  # the non-function lines are covered by windows
    assert any(c.symbol == "m.f" for c in chunks)


def test_tokens_included_for_keyword_search():
    source = "def calculate_price():\n    return 1\n"
    symbols = [ParsedSymbol("calculate_price", "m.calculate_price", EntityType.FUNCTION, 1, 2, parent_qname="m")]
    chunks = _chunks(source, symbols)
    target = next(c for c in chunks if c.symbol == "m.calculate_price")
    assert "calculate" in target.tokens and "price" in target.tokens


def test_config_files_are_never_chunked():
    # config bodies (secrets!) must not enter chunks/vectors (recommendations §5.1)
    from uci.embeddings.chunking import build_chunks
    out = build_chunks(
        repo_id="r", path=".env", language="config", source="SECRET=abc123xyz\n",
        symbols=[], entity_id_for=lambda s: "",
    )
    assert out == []


def test_secret_scrubbing_masks_credentials():
    from uci.embeddings.chunking import scrub_secrets
    assert "REDACTED" in scrub_secrets("aws = 'AKIAIOSFODNN7EXAMPLE'")
    assert "REDACTED" in scrub_secrets("api_key = 'sk-supersecretvalue123'")
    assert "-----BEGIN" not in scrub_secrets("-----BEGIN RSA PRIVATE KEY-----")
    assert scrub_secrets("x = 1") == "x = 1"
