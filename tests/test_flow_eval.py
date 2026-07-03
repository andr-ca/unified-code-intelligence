"""CI gate for the flow-level block scheme: runs evals/flow_eval.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import flow_eval as E  # noqa: E402


def test_flow_builder_is_structurally_correct_on_all_fixtures():
    report = E.run()
    assert report["overall"] == 100.0, report
    for f in report["fixtures"]:
        assert not f["failed"], (f["anchor"], f["failed"])


def test_flow_eval_covers_every_anchor_kind():
    report = E.run()
    assert {f["anchor"] for f in report["fixtures"]} == {"transaction", "job", "capability"}
