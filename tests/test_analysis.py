"""Analysis tests: overview, architecture inference, module explanation, onboarding."""

from __future__ import annotations


def test_overview_totals(indexed_engine):
    data = indexed_engine.overview()
    assert data["totals"]["classes"] >= 3
    assert data["totals"]["tests"] >= 1
    assert "python" in data["languages"]
    assert data["key_symbols"]  # something is called


def test_architecture_layers(indexed_engine):
    data = indexed_engine.architecture()
    names = {layer["name"] for layer in data["layers"]}
    assert "Test" in names  # tests/ directory detected
    assert data["layers"]


def test_explain_module(indexed_engine):
    data = indexed_engine.explain_module("pricing.calculator")
    assert data["ok"]
    assert any(s["name"] == "PricingCalculator" for s in data["symbols"])
    assert data["layer"]
    assert data["purpose"]


def test_onboarding_guide(indexed_engine):
    data = indexed_engine.onboarding()
    assert data["steps"]
    assert data["markdown"].startswith("# Onboarding")
    assert data["key_concepts"]


def test_layer_for_path():
    from uci.analysis.architecture import layer_for_path

    assert layer_for_path("api/routes.py")[0] == "API"
    assert layer_for_path("tests/test_x.py")[0] == "Test"
    assert layer_for_path("models/user.py")[0] == "Data"
    assert layer_for_path("random/thing.py")[0] == "Core"
