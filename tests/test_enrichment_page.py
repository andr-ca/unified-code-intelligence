"""Enrichment dashboard bridge: the architecture pass is listed and evaluated."""

from __future__ import annotations

from pathlib import Path

from uci import Config, Engine
from uci.api import enrichment


def test_architecture_is_a_listed_pass():
    # the dashboard enumerates PASSES for its checkboxes and filters /api/enrich against it
    assert "architecture" in enrichment.PASSES


def test_evaluate_reports_architecture_presence(tmp_path: Path):
    repo = tmp_path / "r"
    repo.mkdir()
    eng = Engine(Config.from_env(repo))
    try:
        ev = enrichment.evaluate(eng)
        assert "architecture" in ev
        assert ev["architecture"]["present"] is False        # not generated yet
        eng.metadata.set_state(eng.repo_id, "architecture_summary", {
            "overview": "A demo system.", "key_points": ["a", "b"],
            "llm": {"model": "qwen3.6", "pass": "architecture"}})
        ev2 = enrichment.evaluate(eng)
        assert ev2["architecture"]["present"] is True
        assert ev2["architecture"]["model"] == "qwen3.6"
        assert ev2["architecture"]["key_points"] == 2
    finally:
        eng.close()
