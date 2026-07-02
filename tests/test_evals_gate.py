"""CI gate: the committed eval baseline must still hold.

Runs the *full* evaluation suite (both tracks) via ``evals/run_eval.py`` and asserts the
``--baseline`` gate passes (exit 0): the ``supported`` (Python/JS) track must not regress
(>1.0-pt track drop or >0.05 category drop), while ``mainframe`` is only a progress meter.

The runner is used unmodified (it is the canonical gate per ``evals/docs/scoring.md``); this test
just drives it and stays idempotent by deleting the timestamped report each run produces.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "evals" / "run_eval.py"
BASELINE = ROOT / "evals" / "reports" / "baseline.json"
REPORTS = ROOT / "evals" / "reports"
FIXTURES = ROOT / "evals" / "fixtures"


@pytest.mark.skipif(
    not (RUNNER.exists() and BASELINE.exists() and FIXTURES.exists()),
    reason="eval suite or committed baseline not present",
)
def test_eval_baseline_gate_passes():
    """The supported eval track must not regress against the committed baseline."""
    before = {p.name for p in REPORTS.glob("run-*")}
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    try:
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--baseline", str(BASELINE), "--clean"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
        )
    finally:
        # keep the suite idempotent — drop the fresh timestamped report this run wrote
        for path in REPORTS.glob("run-*"):
            if path.name not in before:
                path.unlink()

    assert proc.returncode == 0, (
        "eval baseline gate failed — the supported track regressed vs "
        f"evals/reports/baseline.json.\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
