"""Retrieval tests: hybrid search, graceful degradation without embeddings, explainability."""

from __future__ import annotations

from uci.core.entities import EntityType


def test_search_returns_relevant_results(indexed_engine):
    result = indexed_engine.search("calculate pricing", top_k=5)
    assert result["ok"] and result["results"]
    names = {r["name"] for r in result["results"]}
    assert any("calc" in n.lower() or "pricing" in n.lower() or "discount" in n.lower() for n in names)


def test_every_hit_is_explainable(indexed_engine):
    result = indexed_engine.search("discount rule", top_k=5)
    for hit in result["results"]:
        assert hit["reason"]
        assert hit["signals"]
        assert 0.0 <= hit["confidence"] <= 1.0
        assert "entity_id" in hit and "path" in hit


def test_search_works_without_embeddings(noop_engine):
    # semantic signal disabled — keyword/symbol/graph still find results
    result = noop_engine.search("PricingCalculator", top_k=5)
    assert result["results"]
    assert result["stats"]["embeddings"] is False
    for hit in result["results"]:
        assert "semantic" not in hit["signals"]


def test_search_envelope_reports_signal_and_staleness(indexed_engine):
    result = indexed_engine.search("pricing", top_k=3)
    # hash embeddings are honestly labeled as lexical-hash, not semantic (recommendations §6.1)
    assert result["stats"]["semantic_signal"] == "lexical-hash"
    assert "index" in result and "generation" in result["index"]
    assert result["index"]["generation"] >= 1


def test_symbol_query_finds_exact_symbol(indexed_engine):
    result = indexed_engine.find_symbol("PricingCalculator", exact=False)
    quals = {r["qualified_name"] for r in result["results"]}
    assert "pricing.calculator.PricingCalculator" in quals


def test_search_kind_filter(indexed_engine):
    result = indexed_engine.search("calculate", top_k=10, kinds=[EntityType.CLASS])
    assert all(r["kind"] == "class" for r in result["results"])


def _doc_repo_engine(tmp_path, overrides=None):
    from uci import Config, Engine

    (tmp_path / "cbl").mkdir()
    (tmp_path / "cbl" / "COSGN00C.cbl").write_text(
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. COSGN00C.\n"
        "       PROCEDURE DIVISION.\n           MOVE 1 TO X.\n")
    (tmp_path / "README.md").write_text(
        "# App\n\n## Signon — COSGN00C\n\n`COSGN00C` validates users at signon time.\n")
    eng = Engine(Config.from_env(tmp_path, overrides or {}))
    eng.index(full=True)
    return eng


def test_doc_hits_labeled_and_weighted(tmp_path):
    eng = _doc_repo_engine(tmp_path)
    try:
        hits = eng.search("signon validates users")["results"]
        doc_hits = [h for h in hits if h["kind"] == "doc_section"]
        assert doc_hits, "doc sections must be searchable"
        assert any("Documentation" in h.get("reason", "") for h in doc_hits)
    finally:
        eng.close()


def test_doc_weight_zero_suppresses_docs(tmp_path):
    eng = _doc_repo_engine(tmp_path, overrides={"weight_doc": 0.0})
    try:
        hits = eng.search("signon validates users")["results"]
        assert all(h["kind"] != "doc_section" for h in hits)
    finally:
        eng.close()


def test_graph_expansion_bridges_docs_and_code(tmp_path):
    eng = _doc_repo_engine(tmp_path)
    try:
        hits = eng.search("Signon — COSGN00C")["results"]
        kinds = {h["kind"] for h in hits}
        assert "legacy_program" in kinds        # DESCRIBES expansion / symbol match pulls the program
    finally:
        eng.close()
