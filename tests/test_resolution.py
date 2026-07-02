"""Resolution-ladder tests: import-traced precision, receiver inference, fan-out cap + unresolved."""

from __future__ import annotations

from uci import Config, Engine
from uci.core.relationships import RESOLVED_LEVELS, RelationType
from uci.ingest.graph_builder import FileParse, GraphBuilder
from uci.ingest.langdetect import module_qname
from uci.parser.python_parser import PythonParser


def _build(files: dict[str, str]):
    parser = PythonParser()
    fps = []
    for path, src in files.items():
        mod = module_qname(path)
        fps.append(FileParse(path, "python", mod, parser.parse(src, path, mod)))
    builder = GraphBuilder("r", "repo", "/root")
    entities, rels = builder.build(fps)
    return builder, {e.qualified_name: e for e in entities}, rels


def _engine(tmp_path, files: dict[str, str]) -> Engine:
    for path, src in files.items():
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    eng = Engine(Config.from_env(tmp_path))
    eng.index(full=True)
    return eng


def _calls(rels):
    return [r for r in rels if r.type == RelationType.CALLS]


def test_import_traced_resolution_is_precise():
    _, ents, rels = _build({
        "mod.py": "def helper():\n    return 1\n",
        "main.py": "from mod import helper\n\ndef go():\n    return helper()\n",
    })
    calls = _calls(rels)
    edge = next(r for r in calls if r.attributes["callee"] == "helper")
    assert edge.attributes["resolution"] == "import-traced"
    assert edge.provenance.confidence >= 0.95
    assert ents["main.go"].id == edge.src_id and ents["mod.helper"].id == edge.dst_id


def test_inferred_receiver_type_resolution():
    _, _, rels = _build({
        "svc.py": (
            "class Service:\n    def run(self):\n        return 1\n\n"
            "def go():\n    s = Service()\n    return s.run()\n"
        ),
    })
    edge = next(r for r in _calls(rels) if r.attributes["callee"] == "run")
    assert edge.attributes["resolution"] == "inferred"


def test_fan_out_cap_drops_edge_and_records_unresolved():
    classes = "\n".join(
        f"class C{i}:\n    def run(self):\n        return {i}\n" for i in range(7)
    )
    caller = "def go(x):\n    return x.run()\n"
    builder, _, rels = _build({"many.py": classes + "\n" + caller})
    # 7 candidates for `run` exceeds the fan-out cap of 5 -> no edge, recorded as unresolved
    run_edges = [r for r in _calls(rels) if r.attributes["callee"] == "run"]
    assert run_edges == []
    assert any(u["name"] == "run" and u["reason"] == "fan-out-capped" for u in builder.unresolved_calls)


def test_all_call_edges_carry_resolution_and_fanout():
    _, _, rels = _build({
        "mod.py": "def helper():\n    return 1\n",
        "main.py": "from mod import helper\n\ndef go():\n    return helper()\n",
    })
    for edge in _calls(rels):
        assert "resolution" in edge.attributes
        assert "fan_out" in edge.attributes
        # confidence is derived from the resolution level, not invented
        if edge.attributes["resolution"] in RESOLVED_LEVELS:
            assert edge.provenance.confidence >= 0.9


# --- Phase 3 (recommendations §11) ---------------------------------------------

def test_ambiguous_import_candidates_are_not_resolved():
    """Finding 11.1(1): ambiguous imported candidates must be 'candidate', not 'import-traced'."""
    _, _, rels = _build({
        "a.py": "def run():\n    return 1\n",
        "b.py": "def run():\n    return 2\n",
        "main.py": "import a\nimport b\n\ndef go(x):\n    return x.run()\n",
    })
    run_edges = [r for r in _calls(rels) if r.attributes["callee"] == "run"]
    assert run_edges  # kept at depth 1
    for edge in run_edges:
        assert edge.attributes["resolution"] == "candidate"
        assert edge.attributes["resolution"] not in RESOLVED_LEVELS
        assert edge.provenance.confidence <= 0.4


def test_inheritance_edges_carry_resolution():
    """Finding 11.2(4): EXTENDS/IMPLEMENTS edges are labeled by the resolution ladder."""
    _, _, rels = _build({
        "base.py": "class Base:\n    pass\n",
        "child.py": "from base import Base\n\nclass Child(Base):\n    pass\n",
    })
    ext = [r for r in rels if r.type == RelationType.EXTENDS]
    assert ext and ext[0].attributes.get("resolution") == "import-traced"


def test_ambiguous_base_class_degrades_below_resolved():
    """Finding 11.2(4): a bare base name matching two classes must not be a resolved EXTENDS."""
    builder, _, rels = _build({
        "a.py": "class Base:\n    pass\n",
        "b.py": "class Base:\n    pass\n",
        "child.py": "class Child(Base):\n    pass\n",
    })
    ext = [r for r in rels if r.type == RelationType.EXTENDS]
    assert ext
    assert ext[0].attributes["resolution"] == "candidate"
    assert ext[0].attributes["resolution"] not in RESOLVED_LEVELS


def test_speculative_edge_does_not_drive_multihop(tmp_path):
    """Finding 11.1(2): a node reached via a speculative edge must not seed deeper traversal."""
    eng = _engine(tmp_path, {
        "a.py": "def helper():\n    return 1\n\ndef run():\n    return helper()\n",
        "b.py": "def run():\n    return 2\n",
        "c.py": "import a\nimport b\n\ndef entry(x):\n    return x.run()\n",
    })
    result = eng.callees("entry", depth=2)
    names = {r["name"] for r in result["results"]}
    assert "run" in names          # candidate edge is reported at depth 1
    assert "helper" not in names   # but the speculative hop is not expanded to depth 2
    assert result["completeness"]["level"] == "partial"
    eng.close()


def test_dynamic_dispatch_prevents_exact_completeness(tmp_path):
    """Finding 11.2(5)/11.1(7): a hidden dynamic caller must keep completeness off 'exact'."""
    classes = "".join(f"class C{i}:\n    def process(self):\n        return {i}\n\n" for i in range(6))
    eng = _engine(tmp_path, {"many.py": classes + "def run(h):\n    return h.process()\n"})
    # 6 same-named candidates -> the dynamic call is dropped and recorded as unresolved
    imp = eng.impact("C0.process")
    assert imp["completeness"]["level"] != "exact"
    callers = eng.callers("C0.process")
    assert callers["completeness"]["level"] != "exact"
    assert callers["completeness"]["unresolved_sites"] >= 1
    eng.close()


def test_find_symbol_exact_has_no_fuzzy_fallback(tmp_path):
    """Finding 11.4(12): exact=True returns nothing rather than surprising fuzzy matches."""
    eng = _engine(tmp_path, {"m.py": "def calculate_total():\n    return 1\n"})
    assert eng.find_symbol("calculate_total", exact=True)["results"]
    assert eng.find_symbol("calculate", exact=True)["results"] == []   # no fuzzy fallback
    assert eng.find_symbol("calculate", exact=False)["results"]        # fuzzy still works
    eng.close()


def test_weights_configurable_via_env(monkeypatch, tmp_path):
    """Finding 11.3(9): retrieval weights are actually read from the environment."""
    monkeypatch.setenv("UCI_WEIGHT_SYMBOL", "2.5")
    monkeypatch.setenv("UCI_RRF_K", "42")
    cfg = Config.from_env(tmp_path)
    assert cfg.weight_symbol == 2.5 and cfg.rrf_k == 42


# --- Phase 4 (recommendations §12) ---------------------------------------------

def test_aliased_base_class_resolves_via_binds():
    """Finding 12.1: `from base import Base as B; class Child(B)` must still produce EXTENDS."""
    _, ents, rels = _build({
        "base.py": "class Base:\n    pass\n",
        "child.py": "from base import Base as B\n\nclass Child(B):\n    pass\n",
    })
    ext = [r for r in rels if r.type == RelationType.EXTENDS]
    assert ext, "aliased base class must still produce an EXTENDS edge"
    assert ext[0].attributes["resolution"] == "import-traced"
    assert ext[0].dst_id == ents["base.Base"].id


def test_get_callees_counts_hidden_callees(tmp_path):
    """Finding 12.3: a function full of dynamic dispatch must not report exact callee completeness."""
    classes = "".join(f"class C{i}:\n    def process(self):\n        return {i}\n\n" for i in range(6))
    eng = _engine(tmp_path, {
        "many.py": classes + "def caller():\n    h = get_handler()\n    return h.process()\n"
    })
    result = eng.callees("caller")
    assert result["completeness"]["level"] == "partial"
    assert result["completeness"]["unresolved_sites"] >= 1
    eng.close()


# --- Phase 5 (recommendations §13) ---------------------------------------------

def test_impact_callees_unresolved_matches_get_callees(tmp_path):
    """Finding 13.1: the impact pack's callees stratum reports hidden callees like get_callees does."""
    classes = "".join(f"class C{i}:\n    def process(self):\n        return {i}\n\n" for i in range(6))
    eng = _engine(tmp_path, {
        "many.py": classes + "def caller():\n    h = get_handler()\n    return h.process()\n"
    })
    imp = eng.impact("caller")
    gc = eng.callees("caller")
    assert imp["callees"]["unresolved"]["count"] > 0
    assert imp["completeness"]["level"] != "exact"
    # both surfaces answer the same question the same way
    assert (imp["completeness"]["level"] != "exact") == (gc["completeness"]["level"] != "exact")
    eng.close()


def test_binds_miss_base_does_not_bind_to_unrelated_global(tmp_path):
    """Finding 13.2: `from pkg.missing import Thing; class C(Thing)` must gap, not bind a same-named
    unrelated class."""
    eng = _engine(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/other.py": "class Thing:\n    pass\n",
        "pkg/child.py": "from pkg.missing import Thing\n\nclass Child(Thing):\n    pass\n",
    })
    for rel in eng.graph.relationships(RelationType.EXTENDS):
        src = eng.graph.get_entity(rel.src_id)
        dst = eng.graph.get_entity(rel.dst_id)
        if src and src.name == "Child":
            assert dst.qualified_name != "pkg.other.Thing"      # not the unrelated global
            assert dst.attributes.get("missing")                # points at the missing stub
    assert any(g["name"] == "pkg.missing.Thing" for g in eng.gaps()["gaps"])
    eng.close()


def test_binds_miss_call_does_not_bind_to_unrelated_global(tmp_path):
    """Finding 13.2 (call side): a bound-but-unindexed call target gaps, not a same-named global."""
    eng = _engine(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/other.py": "def helper():\n    return 1\n",
        "pkg/main.py": "from pkg.missing import helper\n\ndef go():\n    return helper()\n",
    })
    for rel in eng.graph.relationships(RelationType.CALLS):
        src = eng.graph.get_entity(rel.src_id)
        if src and src.name == "go":
            assert eng.graph.get_entity(rel.dst_id).qualified_name != "pkg.other.helper"
    assert any(g["name"] == "pkg.missing.helper" for g in eng.gaps()["gaps"])
    eng.close()


def test_external_stub_edges_labeled_external(tmp_path):
    """Finding 13.3: external-base stub edges carry resolution 'external', never 'missing'."""
    eng = _engine(tmp_path, {"m.py": "from pydantic import BaseModel\n\nclass User(BaseModel):\n    pass\n"})
    assert not any(g["name"] == "pydantic.BaseModel" for g in eng.gaps()["gaps"])  # external, no gap
    # invariant: no 'missing'-labeled edge points at an external entity
    for rtype in (RelationType.EXTENDS, RelationType.IMPLEMENTS, RelationType.CALLS,
                  RelationType.REFERENCES, RelationType.IMPORTS):
        for rel in eng.graph.relationships(rtype):
            dst = eng.graph.get_entity(rel.dst_id)
            if dst and dst.attributes.get("external"):
                assert rel.attributes.get("resolution") != "missing"
    eng.close()
