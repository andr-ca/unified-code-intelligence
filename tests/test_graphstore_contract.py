"""GraphStore contract tests — the SAME suite runs against InMemoryGraphStore and SQLiteGraphStore.

This guarantees both backends behave identically (interface substitutability).
"""

from __future__ import annotations

import pytest

from uci.core import Entity, EntityType, Provenance, Relationship, RelationType
from uci.graph.inmemory import InMemoryGraphStore
from uci.store.sqlite_backend import SqliteDatabase, SQLiteGraphStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request):
    if request.param == "memory":
        yield InMemoryGraphStore()
    else:
        db = SqliteDatabase(":memory:")
        yield SQLiteGraphStore(db)
        db.close()


def _ent(eid, kind, name, qname, repo="r", path="a.py", line=1):
    return Entity(eid, kind, name, qname, Provenance(repo, path, line, line + 2), {"module": "a"})


def _rel(rid, rtype, s, d, repo="r"):
    return Relationship(rid, rtype, s, d, Provenance(repo, "a.py", 1, 1))


@pytest.fixture
def populated(store):
    a = _ent("f:a", EntityType.FUNCTION, "alpha", "a.alpha")
    b = _ent("f:b", EntityType.FUNCTION, "beta", "a.beta")
    c = _ent("c:c", EntityType.CLASS, "Gamma", "a.Gamma")
    store.add_entities([a, b, c])
    store.add_relationship(_rel("r1", RelationType.CALLS, "f:a", "f:b"))
    store.add_relationship(_rel("r2", RelationType.CALLS, "f:b", "c:c"))
    store.add_relationship(_rel("r3", RelationType.DEFINES, "c:c", "f:a"))
    return store


def test_add_and_get_entity(store):
    store.add_entity(_ent("f:a", EntityType.FUNCTION, "alpha", "a.alpha"))
    got = store.get_entity("f:a")
    assert got is not None and got.name == "alpha"
    assert store.has_entity("f:a")
    assert store.get_entity("missing") is None


def test_entities_filter_by_kind(populated):
    functions = list(populated.entities(kind=EntityType.FUNCTION))
    assert {e.name for e in functions} == {"alpha", "beta"}
    assert populated.count_entities(EntityType.CLASS) == 1


def test_out_and_in_relationships(populated):
    out = populated.out_relationships("f:a", [RelationType.CALLS])
    assert [r.dst_id for r in out] == ["f:b"]
    incoming = populated.in_relationships("f:b", [RelationType.CALLS])
    assert [r.src_id for r in incoming] == ["f:a"]


def test_neighbors_both_directions(populated):
    neighbors = populated.neighbors("f:b", direction="both", rtypes=[RelationType.CALLS])
    names = {e.name for _, e in neighbors}
    assert names == {"alpha", "Gamma"}


def test_bfs_traversal_depth(populated):
    reached = populated.bfs("f:a", direction="out", rtypes=[RelationType.CALLS], max_depth=2)
    names = {e.name for e, _, _ in reached}
    assert names == {"beta", "Gamma"}
    # depth 1 only reaches beta
    shallow = populated.bfs("f:a", direction="out", rtypes=[RelationType.CALLS], max_depth=1)
    assert {e.name for e, _, _ in shallow} == {"beta"}


def test_find_by_name_exact_and_fuzzy(populated):
    assert [e.id for e in populated.find_by_name("alpha", exact=True)] == ["f:a"]
    assert [e.id for e in populated.find_by_name("a.alpha", exact=True)] == ["f:a"]
    fuzzy = populated.find_by_name("alph", exact=False)
    assert "f:a" in {e.id for e in fuzzy}
    typed = populated.find_by_name("Gamma", kind=EntityType.CLASS, exact=True)
    assert len(typed) == 1


def test_relationships_filter_by_type(populated):
    calls = list(populated.relationships(RelationType.CALLS))
    assert len(calls) == 2
    assert populated.count_relationships(RelationType.DEFINES) == 1


def test_duplicate_relationship_is_idempotent(store):
    store.add_entity(_ent("f:a", EntityType.FUNCTION, "a", "a"))
    store.add_entity(_ent("f:b", EntityType.FUNCTION, "b", "b"))
    store.add_relationship(_rel("r1", RelationType.CALLS, "f:a", "f:b"))
    store.add_relationship(_rel("r1", RelationType.CALLS, "f:a", "f:b"))
    assert store.count_relationships(RelationType.CALLS) == 1


def test_clear_by_repo(store):
    store.add_entity(_ent("f:a", EntityType.FUNCTION, "a", "a", repo="r1"))
    store.add_entity(_ent("f:b", EntityType.FUNCTION, "b", "b", repo="r2"))
    store.clear(repo_id="r1")
    assert store.get_entity("f:a") is None
    assert store.get_entity("f:b") is not None
    store.clear()
    assert store.count_entities() == 0
