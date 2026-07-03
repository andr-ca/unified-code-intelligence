"""Flow-level block scheme (Tier-1): build_flow / resolve_roots + engine.flow."""

from __future__ import annotations

from pathlib import Path

from uci import Config, Engine
from uci.analysis.flow import build_flow, resolve_roots
from uci.core import Entity, EntityType, Provenance, Relationship, RelationType
from uci.core.ids import relationship_id
from uci.graph.inmemory import InMemoryGraphStore


def _e(eid, kind, name):
    return Entity(eid, kind, name, name, Provenance("r", "x.cbl", 1, 1), {})


def _rel(t, s, d):
    return Relationship(relationship_id(t, s, d), t, s, d, Provenance("r", "x.cbl", 1, 1))


def _mainframe_graph():
    g = InMemoryGraphStore()
    g.add_entities([
        _e("t1", EntityType.TRANSACTION_CODE, "TX01"),
        _e("pa", EntityType.LEGACY_PROGRAM, "PROGA"),
        _e("pb", EntityType.LEGACY_PROGRAM, "PROGB"),
        _e("ac", EntityType.DATABASE_TABLE, "ACCT"),
        _e("sc", EntityType.SCREEN, "MENU"),
    ])
    for t, s, d in [(RelationType.INVOKES, "t1", "pa"), (RelationType.CALLS, "pa", "pb"),
                    (RelationType.READS, "pa", "ac"), (RelationType.WRITES, "pb", "ac"),
                    (RelationType.USES, "pa", "sc")]:
        g.add_relationship(_rel(t, s, d))
    return g


def test_build_flow_from_transaction():
    g = _mainframe_graph()
    fg = build_flow(g, [g.get_entity("t1")], depth=3)
    st = fg.stats()
    assert st["programs"] == 2 and st["data"] == 1 and st["screens"] == 1
    labels = {(n.kind, n.label) for n in fg.nodes}
    assert (EntityType.TRANSACTION_CODE.value, "TX01") in labels
    assert (EntityType.DATABASE_TABLE.value, "ACCT") in labels
    edge_labels = {e.label for e in fg.edges}
    assert {"invokes", "calls", "reads", "writes", "uses"} <= edge_labels
    # data/screen leaves have no outgoing edges
    outs = {e.src for e in fg.edges}
    data_ids = {n.id for n in fg.nodes if n.kind in ("database_table", "screen")}
    assert not (data_ids & outs)


def test_build_flow_depth_limits_expansion():
    g = _mainframe_graph()
    fg = build_flow(g, [g.get_entity("t1")], depth=1)
    # depth 1: TX01 → PROGA (and PROGA's own data/screens), but not PROGB (2 hops of control)
    names = {n.label for n in fg.nodes}
    assert "PROGA" in names and "PROGB" not in names


def test_resolve_roots_expands_capability():
    g = InMemoryGraphStore()
    g.add_entities([_e("cap", EntityType.BUSINESS_CAPABILITY, "Payments"),
                    _e("p1", EntityType.LEGACY_PROGRAM, "PAYAUTH"),
                    _e("p2", EntityType.LEGACY_PROGRAM, "PAYRUN")])
    g.add_relationship(_rel(RelationType.IMPLEMENTS_CAPABILITY, "p1", "cap"))
    g.add_relationship(_rel(RelationType.IMPLEMENTS_CAPABILITY, "p2", "cap"))
    roots = resolve_roots(g, g.get_entity("cap"))
    assert {r.name for r in roots} == {"PAYAUTH", "PAYRUN"}


_PROGA = """       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROGA.
       PROCEDURE DIVISION.
           EXEC SQL SELECT ACCT_NO FROM SHOP.ACCT END-EXEC.
           CALL 'PROGB'.
           GOBACK.
"""
_PROGB = "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. PROGB.\n       PROCEDURE DIVISION.\n           GOBACK.\n"


def test_engine_flow_on_indexed_cobol(tmp_path):
    repo = tmp_path / "cob"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cbl" / "PROGA.cbl").write_text(_PROGA, encoding="utf-8")
    (repo / "cbl" / "PROGB.cbl").write_text(_PROGB, encoding="utf-8")
    eng = Engine(Config.from_env(repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    try:
        data = eng.flow("PROGA")
        assert data["ok"] and data["mermaid"].startswith("flowchart LR")
        names = {n["label"] for n in data["nodes"]}
        assert "PROGA" in names and "PROGB" in names  # CALL edge traced
        assert any(n["kind"] == "database_table" for n in data["nodes"])  # EXEC SQL read
    finally:
        eng.close()


def test_engine_flow_not_found(tmp_path):
    repo = tmp_path / "cob"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cbl" / "PROGB.cbl").write_text(_PROGB, encoding="utf-8")
    eng = Engine(Config.from_env(repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    try:
        data = eng.flow("NOSUCH")
        assert not data["ok"] and data["error"]["code"] == "not_found"
    finally:
        eng.close()
