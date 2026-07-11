"""Smoke test for the docs-linkage eval scorer (fast; inline dataset, no demo-repo dependency)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from uci import Config, Engine

ROOT = Path(__file__).resolve().parent.parent
CARDDEMO = ROOT / "evals" / "demo-repos" / "aws-mainframe-modernization-carddemo"
DATASET = ROOT / "evals" / "datasets" / "doc_links" / "carddemo.json"


def _load_module():
    spec = importlib.util.spec_from_file_location("docs_eval", ROOT / "evals" / "docs_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_docs_eval_scores_hit_miss_forbidden(tmp_path):
    score = _load_module().score
    (tmp_path / "cbl").mkdir()
    (tmp_path / "cbl" / "COSGN00C.cbl").write_text(
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. COSGN00C.\n"
        "       PROCEDURE DIVISION.\n           MOVE 1 TO X.\n")
    (tmp_path / "README.md").write_text(
        "# App\n\n## Signon — COSGN00C\n\n`COSGN00C` handles signon.\n")
    dataset = {
        "name": "smoke",
        "expected_links": [
            {"doc_path": "README.md", "section_contains": "Signon", "target": "COSGN00C"},   # hit
            {"doc_path": "README.md", "section_contains": "Signon", "target": "NOSUCHPGM"},   # miss
        ],
        "forbidden_links": [{"doc_path": "README.md", "target": "COBOL"}],                    # absent = good
    }
    with Engine(Config.from_env(tmp_path)) as eng:
        eng.index(full=True)
        result = score(eng, dataset)
    assert result["found"] == 1 and result["expected"] == 2
    assert result["recall"] == 0.5
    assert result["forbidden_hits"] == []       # "COBOL" is stoplisted, never linked
    assert result["precision"] == 1.0
    assert result["gate"] == "FAIL"             # recall 0.5 < 0.8


@pytest.mark.skipif(not (CARDDEMO.exists() and DATASET.exists()),
                    reason="carddemo demo repo or dataset not present")
def test_carddemo_doc_links_gate_passes():
    """The hand-labeled CardDemo golden must pass the precision/recall gate."""
    result = _load_module().run_dataset(DATASET)
    assert result["gate"] == "PASS", result
    assert result["precision"] >= 0.9 and result["recall"] >= 0.8
