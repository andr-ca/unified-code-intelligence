"""CI gate for the LSP edge-oracle bridge: runs evals/lsp_eval.py end-to-end.

This spawns the scripted LSP server (`evals/tools/scripted_lsp.py`) as a real subprocess and drives
verify/discover/complete through the actual `LspClient` stdio transport — so it regression-tests the
whole bridge (framing, spawn, promote/prune/discover/complete/leave) with no toolchain installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import lsp_eval as E  # noqa: E402


def test_lsp_bridge_eval_is_perfect_on_golden_fixture():
    report = E.run()
    assert report["overall"] == 100.0, report
    assert report["left_uncertain_ok"] is True
    for mode, s in report["scores"].items():
        assert s["f1"] == 1.0, (mode, s)


def test_lsp_bridge_eval_exercises_every_mode():
    report = E.run()
    # each mode actually issued queries / produced predictions (not a vacuous pass)
    assert report["queried"]["verify"] == 3      # 3 speculative edges checked
    assert report["queried"]["discover"] == 1    # 1 worklist site
    assert report["queried"]["complete"] == 1    # 1 high-value symbol
    assert report["scores"]["complete"]["tp"] == 2  # 2 reference edges built
