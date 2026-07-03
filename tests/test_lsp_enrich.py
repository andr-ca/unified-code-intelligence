"""Tests for the optional LSP/SCIP edge oracles (docs/lsp-refactoring-recommendations.md).

The LSP client's wire framing is tested over in-memory byte streams; the EdgeSources are tested
against an InMemoryGraphStore with a FakeLspClient / a hand-encoded .scip blob — so the promote /
prune / discover logic is verified with no real language server or toolchain installed.
"""

from __future__ import annotations

import io
from pathlib import Path

from uci.core import Entity, EntityType, Provenance, Relationship, RelationType
from uci.core.ids import relationship_id
from uci.core.relationships import RESOLVED_LEVELS
from uci.enrich.base import Budget, EdgeDelta
from uci.enrich.lsp_client import LspError, encode_message, path_to_uri, read_message
from uci.enrich.lsp_source import LspEdgeSource
from uci.enrich.scip_source import ScipSource, parse_scip_index
from uci.graph.inmemory import InMemoryGraphStore


# ---------------------------------------------------------------- framing
def test_lsp_framing_roundtrip():
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"x": "ünïcode"}}
    stream = io.BytesIO(encode_message(msg))
    assert read_message(stream) == msg
    assert read_message(stream) is None  # clean EOF → None


def test_lsp_read_message_truncated_body_raises():
    raw = encode_message({"id": 1})[:-3]  # chop the body
    try:
        read_message(io.BytesIO(raw))
    except LspError:
        return
    raise AssertionError("expected LspError on truncated body")


# ---------------------------------------------------------------- fake LSP
class FakeLspClient:
    """Duck-typed LspClient returning canned definition / references Locations."""

    def __init__(self, definition_uri: str | None, def_line0: int = 0,
                 references: list[dict] | None = None):
        self._uri = definition_uri
        self._line = def_line0
        self._refs = references or []
        self.opened: list[str] = []
        self.queries = 0

    def did_open(self, path, language_id, text):
        self.opened.append(path)

    def definition(self, path, line, character):
        self.queries += 1
        if self._uri is None:
            return None
        return {"uri": self._uri, "range": {"start": {"line": self._line, "character": 0}}}

    def references(self, path, line, character):
        self.queries += 1
        return list(self._refs)


def _loc(uri: str, line0: int) -> dict:
    return {"uri": uri, "range": {"start": {"line": line0, "character": 0}}}


def _prog(eid, name, path, line=1):
    return Entity(eid, EntityType.LEGACY_PROGRAM, name, name,
                  Provenance("r", path, line, line), {})


def _speculative_call(src, dst, path, line):
    return Relationship(
        id=relationship_id(RelationType.CALLS, src.id, dst.id, ordinal=line),
        type=RelationType.CALLS, src_id=src.id, dst_id=dst.id,
        provenance=Provenance("r", path, line, line, "cobol_parser", 0.5),
        attributes={"resolution": "name-match"})


def _cobol_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cbl" / "MAIN.cbl").write_text(
        "       PROGRAM-ID. MAIN.\n"
        "       PROCEDURE DIVISION.\n"
        "           CALL 'HELPER'.\n", encoding="utf-8")
    (repo / "cbl" / "HELPER.cbl").write_text("       PROGRAM-ID. HELPER.\n", encoding="utf-8")
    (repo / "cbl" / "OTHER.cbl").write_text("       PROGRAM-ID. OTHER.\n", encoding="utf-8")
    return repo


def test_lsp_verify_promotes_confirmed_edge(tmp_path):
    repo = _cobol_repo(tmp_path)
    graph = InMemoryGraphStore()
    main = _prog("p:main", "MAIN", "cbl/MAIN.cbl")
    helper = _prog("p:helper", "HELPER", "cbl/HELPER.cbl")
    graph.add_entities([main, helper])
    graph.add_relationship(_speculative_call(main, helper, "cbl/MAIN.cbl", 3))

    client = FakeLspClient(path_to_uri(repo / "cbl" / "HELPER.cbl"), def_line0=0)
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.verify(graph, "r")

    assert len(delta.promoted) == 1 and not delta.pruned
    rel = delta.promoted[0]
    assert rel.attributes["resolution"] == "lsp-verified"
    assert rel.attributes["resolution"] in RESOLVED_LEVELS
    assert rel.provenance.confidence == 0.95
    assert rel.attributes["verified_by"].startswith("lsp:cobol@")


def test_lsp_verify_prunes_edge_resolving_elsewhere(tmp_path):
    repo = _cobol_repo(tmp_path)
    graph = InMemoryGraphStore()
    main = _prog("p:main", "MAIN", "cbl/MAIN.cbl")
    helper = _prog("p:helper", "HELPER", "cbl/HELPER.cbl")
    other = _prog("p:other", "OTHER", "cbl/OTHER.cbl")
    graph.add_entities([main, helper, other])
    graph.add_relationship(_speculative_call(main, helper, "cbl/MAIN.cbl", 3))

    # server says the call at MAIN.cbl:3 actually resolves to OTHER.cbl — the HELPER edge is false
    client = FakeLspClient(path_to_uri(repo / "cbl" / "OTHER.cbl"), def_line0=0)
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.verify(graph, "r")

    assert len(delta.pruned) == 1 and not delta.promoted
    assert delta.pruned[0].attributes["pruned"] is True
    assert delta.pruned[0].attributes["pruned_by"].startswith("lsp:cobol@")


def test_lsp_verify_leaves_edge_on_no_definition(tmp_path):
    repo = _cobol_repo(tmp_path)
    graph = InMemoryGraphStore()
    main = _prog("p:main", "MAIN", "cbl/MAIN.cbl")
    helper = _prog("p:helper", "HELPER", "cbl/HELPER.cbl")
    graph.add_entities([main, helper])
    graph.add_relationship(_speculative_call(main, helper, "cbl/MAIN.cbl", 3))

    client = FakeLspClient(None)  # server unsure
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.verify(graph, "r")
    assert not delta.promoted and not delta.pruned and delta.queried == 1


def test_lsp_verify_skips_already_resolved_edges(tmp_path):
    repo = _cobol_repo(tmp_path)
    graph = InMemoryGraphStore()
    main = _prog("p:main", "MAIN", "cbl/MAIN.cbl")
    helper = _prog("p:helper", "HELPER", "cbl/HELPER.cbl")
    graph.add_entities([main, helper])
    rel = _speculative_call(main, helper, "cbl/MAIN.cbl", 3)
    rel.attributes["resolution"] = "syntactic"  # already provable → not a verify candidate
    graph.add_relationship(rel)

    client = FakeLspClient(path_to_uri(repo / "cbl" / "HELPER.cbl"))
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.verify(graph, "r")
    assert delta.queried == 0 and not delta


def test_lsp_budget_caps_queries(tmp_path):
    repo = _cobol_repo(tmp_path)
    graph = InMemoryGraphStore()
    main = _prog("p:main", "MAIN", "cbl/MAIN.cbl")
    helper = _prog("p:helper", "HELPER", "cbl/HELPER.cbl")
    graph.add_entities([main, helper])
    graph.add_relationship(_speculative_call(main, helper, "cbl/MAIN.cbl", 3))

    client = FakeLspClient(path_to_uri(repo / "cbl" / "HELPER.cbl"))
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.verify(graph, "r", budget=Budget(max_queries=0))
    assert delta.queried == 0 and client.queries == 0


def test_lsp_source_unavailable_without_toolchain(tmp_path):
    # no injected client and no UCI_LSP_COBOL_CMD → binary not on PATH → unavailable, empty verify
    src = LspEdgeSource("cobol", str(tmp_path), settings={})
    assert src.available is False
    assert not src.verify(InMemoryGraphStore(), "r")


def test_lsp_discover_creates_edge_from_worklist(tmp_path):
    repo = tmp_path / "repo"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cbl" / "MAIN.cbl").write_text(
        "       PROGRAM-ID. MAIN.\n"
        "       PROCEDURE DIVISION.\n"
        "           CALL WS-PGM.\n", encoding="utf-8")           # dynamic, line 3
    (repo / "cbl" / "POSTER.cbl").write_text("       PROGRAM-ID. POSTER.\n", encoding="utf-8")
    graph = InMemoryGraphStore()
    main = _prog("p:main", "MAIN", "cbl/MAIN.cbl")
    poster = _prog("p:poster", "POSTER", "cbl/POSTER.cbl")
    graph.add_entities([main, poster])
    worklist = [{"path": "cbl/MAIN.cbl", "line": 3, "name": "WS-PGM", "caller": "MAIN",
                 "reason": "dynamic-target"}]

    client = FakeLspClient(path_to_uri(repo / "cbl" / "POSTER.cbl"), def_line0=0)
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.discover(graph, "r", worklist)

    assert len(delta.discovered) == 1
    edge = delta.discovered[0]
    assert edge.type == RelationType.CALLS
    assert edge.src_id == "p:main" and edge.dst_id == "p:poster"
    assert edge.attributes["resolution"] == "lsp-verified"
    assert edge.attributes["mode"] == "discover" and edge.attributes["via"] == "WS-PGM"


def test_lsp_discover_skips_unresolvable_site(tmp_path):
    repo = tmp_path / "repo"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cbl" / "MAIN.cbl").write_text(
        "       PROGRAM-ID. MAIN.\n       PROCEDURE DIVISION.\n           CALL WS-PGM.\n",
        encoding="utf-8")
    graph = InMemoryGraphStore()
    graph.add_entities([_prog("p:main", "MAIN", "cbl/MAIN.cbl")])
    worklist = [{"path": "cbl/MAIN.cbl", "line": 3, "name": "WS-PGM", "caller": "MAIN"}]

    client = FakeLspClient(None)  # server can't resolve the dynamic target
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.discover(graph, "r", worklist)
    assert not delta.discovered and delta.queried == 1


def test_lsp_complete_adds_reference_edges(tmp_path):
    repo = tmp_path / "repo"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cbl" / "HELPER.cbl").write_text("       PROGRAM-ID. HELPER.\n", encoding="utf-8")
    (repo / "cbl" / "A.cbl").write_text(
        "       PROGRAM-ID. A.\n       PROCEDURE DIVISION.\n           CALL 'HELPER'.\n", "utf-8")
    (repo / "cbl" / "B.cbl").write_text(
        "       PROGRAM-ID. B.\n           CALL 'HELPER'.\n", "utf-8")
    graph = InMemoryGraphStore()
    helper = _prog("p:helper", "HELPER", "cbl/HELPER.cbl")
    a = _prog("p:a", "A", "cbl/A.cbl")
    b = _prog("p:b", "B", "cbl/B.cbl")
    graph.add_entities([helper, a, b])

    refs = [_loc(path_to_uri(repo / "cbl" / "A.cbl"), 2),   # A.cbl line 3
            _loc(path_to_uri(repo / "cbl" / "B.cbl"), 1)]   # B.cbl line 2
    client = FakeLspClient(None, references=refs)
    src = LspEdgeSource("cobol", str(repo), client=client)
    delta = src.complete(graph, "r", [helper])

    assert len(delta.discovered) == 2
    edges = {(e.src_id, e.dst_id) for e in delta.discovered}
    assert edges == {("p:a", "p:helper"), ("p:b", "p:helper")}
    assert all(e.type == RelationType.REFERENCES for e in delta.discovered)
    assert all(e.attributes["mode"] == "complete" for e in delta.discovered)


# ---------------------------------------------------------------- SCIP
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _ld(field: int, payload: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(payload)) + payload


def _pstr(field: int, s: str) -> bytes:
    return _ld(field, s.encode("utf-8"))


def _pvint(field: int, n: int) -> bytes:
    return _tag(field, 0) + _varint(n)


def _packed(field: int, ints: list[int]) -> bytes:
    return _ld(field, b"".join(_varint(i) for i in ints))


def _occurrence(rng: list[int], symbol: str, roles: int) -> bytes:
    return _packed(1, rng) + _pstr(2, symbol) + _pvint(3, roles)


def _document(path: str, occs: list[bytes]) -> bytes:
    return _pstr(1, path) + b"".join(_ld(2, o) for o in occs)


def _index(docs: list[bytes]) -> bytes:
    return b"".join(_ld(2, d) for d in docs)


def test_scip_parse_roundtrip():
    blob = _index([
        _document("lib.py", [_occurrence([0, 4, 10], "scip py . lib/foo().", 0x1)]),
        _document("app.py", [_occurrence([5, 2, 8], "scip py . lib/foo().", 0x0)]),
    ])
    docs = parse_scip_index(blob)
    assert [d.relative_path for d in docs] == ["lib.py", "app.py"]
    assert docs[0].occurrences[0].is_definition
    assert not docs[1].occurrences[0].is_definition
    assert docs[1].occurrences[0].symbol == "scip py . lib/foo()."


def test_scip_discover_builds_edges(tmp_path):
    # graph: APPFUNC (app.py:1) references LIBFUNC (lib.py:1) → expect a CALLS edge
    graph = InMemoryGraphStore()
    libfunc = Entity("f:lib", EntityType.FUNCTION, "foo", "lib.foo",
                     Provenance("r", "lib.py", 1, 5), {})
    appfunc = Entity("f:app", EntityType.FUNCTION, "run", "app.run",
                     Provenance("r", "app.py", 1, 9), {})
    graph.add_entities([libfunc, appfunc])

    sym = "scip py . lib/foo()."
    blob = _index([
        _document("lib.py", [_occurrence([0, 4, 7], sym, 0x1)]),      # definition at lib.py line 0
        _document("app.py", [_occurrence([5, 8, 11], sym, 0x0)]),     # reference at app.py line 5
    ])
    index_path = tmp_path / "index.scip"
    index_path.write_bytes(blob)

    src = ScipSource(str(index_path), str(tmp_path))
    assert src.available
    delta = src.discover(graph, "r")
    assert len(delta.discovered) == 1
    edge = delta.discovered[0]
    assert edge.type == RelationType.CALLS
    assert edge.src_id == "f:app" and edge.dst_id == "f:lib"
    assert edge.attributes["resolution"] == "scip"
    assert edge.attributes["resolution"] in RESOLVED_LEVELS
    assert edge.provenance.confidence == 1.0


def test_scip_unavailable_when_index_missing(tmp_path):
    src = ScipSource(str(tmp_path / "nope.scip"), str(tmp_path))
    assert src.available is False
    assert not src.discover(InMemoryGraphStore(), "r")


def test_scip_corrupt_index_produces_nothing(tmp_path):
    bad = tmp_path / "bad.scip"
    bad.write_bytes(b"\xff\xff not protobuf \x00\x01")
    src = ScipSource(str(bad), str(tmp_path))
    # available (file exists) but undecodable → empty delta, no exception
    assert not src.discover(InMemoryGraphStore(), "r")


# ---------------------------------------------------------------- edge delta
def test_edge_delta_extend_and_counts():
    d = EdgeDelta(queried=2)
    other = EdgeDelta(discovered=[object()], queried=3)  # type: ignore[list-item]
    d.extend(other)
    assert d.counts() == {"promoted": 0, "pruned": 0, "discovered": 1, "queried": 5}
    assert bool(d) is True
