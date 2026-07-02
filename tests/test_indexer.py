"""Indexer integration tests: full pipeline, cross-file resolution, and incremental re-indexing."""

from __future__ import annotations

from pathlib import Path

from uci.core.entities import EntityType
from uci.core.relationships import RelationType


def _rel_pairs(engine, rtype):
    pairs = []
    for rel in engine.graph.relationships(rtype):
        src = engine.graph.get_entity(rel.src_id)
        dst = engine.graph.get_entity(rel.dst_id)
        if src and dst:
            pairs.append((src.name, dst.name))
    return pairs


def test_index_produces_expected_entities(indexed_engine):
    counts = {}
    for e in indexed_engine.graph.entities(repo_id=indexed_engine.repo_id):
        counts[e.kind] = counts.get(e.kind, 0) + 1
    assert counts.get(EntityType.CLASS, 0) >= 3
    assert counts.get(EntityType.METHOD, 0) >= 2
    assert counts.get(EntityType.TEST, 0) >= 1
    assert counts.get(EntityType.CONFIG_KEY, 0) >= 2
    assert counts.get(EntityType.MODULE, 0) >= 4


def test_index_resolves_inheritance(indexed_engine):
    extends = _rel_pairs(indexed_engine, RelationType.EXTENDS)
    assert ("PricingCalculator", "BaseCalculator") in extends


def test_index_resolves_imports(indexed_engine):
    imports = _rel_pairs(indexed_engine, RelationType.IMPORTS)
    # checkout imports pricing.calculator (module-to-module)
    assert any(dst == "calculator" for _, dst in imports)


def test_index_resolves_calls(indexed_engine):
    calls = _rel_pairs(indexed_engine, RelationType.CALLS)
    assert any(callee == "apply" for _, callee in calls)  # calculate -> DiscountRule.apply


def test_all_facts_have_provenance(indexed_engine):
    for e in indexed_engine.graph.entities(repo_id=indexed_engine.repo_id):
        assert e.provenance.repo_id == indexed_engine.repo_id
        if e.kind in (EntityType.FUNCTION, EntityType.METHOD, EntityType.CLASS):
            assert e.provenance.path and e.provenance.start_line >= 1


def test_incremental_reindex_detects_changes(engine, sample_repo: Path):
    engine.index(full=True)
    stats2 = engine.index(full=False)
    assert stats2.files_changed == 0  # nothing changed on second run

    (sample_repo / "checkout.py").write_text(
        "from pricing.calculator import PricingCalculator\n\n"
        "def place_order(cart):\n    return PricingCalculator().calculate(cart)\n\n"
        "def new_fn():\n    return 42\n"
    )
    stats3 = engine.index(full=False)
    assert stats3.files_changed == 1
    assert engine.graph.find_by_name("new_fn", exact=True)


def test_deleted_file_removed_from_graph(engine, sample_repo: Path):
    engine.index(full=True)
    assert engine.graph.find_by_name("computeTotal", exact=True)
    (sample_repo / "web" / "app.js").unlink()
    engine.index(full=False)
    assert not engine.graph.find_by_name("computeTotal", exact=True)
