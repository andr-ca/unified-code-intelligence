"""Impact-analysis and edit-context tests (the flagship graph-native queries)."""

from __future__ import annotations


def test_impact_pack_structure(indexed_engine):
    data = indexed_engine.impact("PricingCalculator.calculate")
    assert data["ok"]
    assert data["target"]["name"] == "calculate"
    caller_names = {c["name"] for c in data["callers"]["resolved"] + data["callers"]["candidates"]}
    assert "place_order" in caller_names
    callee_names = {c["name"] for c in data["callees"]["resolved"] + data["callees"]["candidates"]}
    assert "apply" in callee_names
    assert "risk" in data and data["risk"]["level"] in ("low", "medium", "high")
    assert data["completeness"]["level"] in ("exact", "partial", "heuristic")
    assert "index" in data and "generation" in data["index"]
    assert data["next_queries"]


def test_impact_stratifies_callers_by_resolution(indexed_engine):
    data = indexed_engine.impact("PricingCalculator.calculate")
    # place_order is resolved precisely (constructor type inference), so it is in 'resolved'
    resolved_names = {c["name"] for c in data["callers"]["resolved"]}
    assert "place_order" in resolved_names
    assert "unresolved" in data["callers"] and "count" in data["callers"]["unresolved"]


def test_impact_finds_covering_tests(indexed_engine):
    data = indexed_engine.impact("PricingCalculator.calculate")
    test_names = {t["name"] for t in data["tests"]}
    assert "test_calculate" in test_names


def test_impact_finds_config_dependency(indexed_engine):
    data = indexed_engine.impact("pricing.calculator")
    # MAX_DISCOUNT is referenced in calculator.py
    config_names = {c["name"] for c in data["config"]}
    assert "MAX_DISCOUNT" in config_names


def test_impact_unknown_symbol(indexed_engine):
    data = indexed_engine.impact("does.not.exist")
    assert data["ok"] is False
    assert data["error"]["code"] == "not_found"


def test_edit_context_has_source_and_checklist(indexed_engine):
    data = indexed_engine.edit_context("PricingCalculator.calculate")
    assert data["ok"]
    assert data["target"]["source"]
    assert isinstance(data["checklist"], list) and data["checklist"]
    # callers include source snippets
    assert all("source" in c for c in data["callers"])


def test_provenance_line_ranges_present(indexed_engine):
    data = indexed_engine.impact("PricingCalculator.calculate")
    t = data["target"]
    assert t["start_line"] >= 1 and t["end_line"] >= t["start_line"]
    assert t["path"].endswith("calculator.py")


def test_call_edges_carry_resolution_labels(indexed_engine):
    """Every call edge is tagged with HOW it was resolved (the resolution ladder), so confidence
    is honest and callers can filter provable edges from speculative ones."""
    data = indexed_engine.impact("PricingCalculator.calculate")
    callers = {c["name"]: c for c in data["callers"]["resolved"] + data["callers"]["candidates"]}
    # place_order does `calc = PricingCalculator(); calc.calculate()` — resolved precisely
    assert "place_order" in callers
    assert callers["place_order"]["resolution"] in {"inferred", "import-traced", "syntactic", "name-match"}
    assert callers["place_order"]["confidence"] >= 0.6
    callees = {c["name"]: c for c in data["callees"]["resolved"] + data["callees"]["candidates"]}
    assert callees["apply"]["resolution"]  # DiscountRule.apply resolved via import trace
