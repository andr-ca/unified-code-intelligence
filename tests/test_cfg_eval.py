"""CI gate for the CFG builder: runs evals/cfg_eval.py and asserts structural correctness."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import cfg_eval as E  # noqa: E402


def test_cfg_builder_is_structurally_correct_on_all_fixtures():
    report = E.run()
    assert report["overall"] == 100.0, report
    for f in report["fixtures"]:
        assert not f["failed"], (f["function"], f["failed"])


def test_cfg_eval_covers_every_construct_family():
    report = E.run()
    fns = {f["function"] for f in report["fixtures"]}
    # python: if/elif, loop+break, match, try — cobol: IF/EVALUATE/PERFORM UNTIL/GO TO, inline PERFORM
    assert fns == {"classify", "scan", "route", "guarded", "POST", "INQ"}
    langs = {f["language"] for f in report["fixtures"]}
    assert langs == {"python", "cobol"}
