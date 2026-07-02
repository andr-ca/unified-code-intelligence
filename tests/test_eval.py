from pathlib import Path

from uci.eval.harness import run_dataset

DATASET = Path(__file__).resolve().parent.parent / "evals" / "datasets"


def test_eval_dataset_present():
    assert DATASET.exists(), "golden dataset dir missing"
    assert list(DATASET.glob("*.json")), "no golden dataset files"


def test_eval_meets_thresholds():
    report = run_dataset(DATASET)
    agg = report["aggregate"]
    cg = agg["callgraph"]
    assert cg["precision"] >= 0.9
    assert cg["recall"] >= 0.9
    assert cg["resolution_accuracy"] >= 0.9
    rt = agg["retrieval"]
    assert rt["recall@5"] >= 0.9
    assert rt["mrr"] >= 0.5
    im = agg["impact"]
    assert im["callers"]["recall"] >= 0.9
    assert im["tests"]["recall"] >= 0.9
    assert im["config"]["recall"] >= 0.9


def test_eval_per_resolution_level_is_precise():
    report = run_dataset(DATASET)
    rc = report["fixtures"]["resolve_cases"]["callgraph"]["per_level"]
    for level in ("syntactic", "import-traced", "inferred"):
        if level in rc:
            assert rc[level]["precision"] == 1.0, f"{level} precision regressed"


def test_eval_reports_missing_and_spurious_lists():
    report = run_dataset(DATASET)
    for fixture in report["fixtures"].values():
        cg = fixture["callgraph"]
        assert cg["missing"] == []
        assert cg["spurious"] == []
