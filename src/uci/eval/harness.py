"""Retrieval / call-graph evaluation harness (recommendations §3).

Turns UCI's determinism/retrieval claims into *measurements*:

* **call-graph** precision & recall, reported **per resolution level** + resolution-label accuracy;
* **retrieval** recall@5 / recall@10 / MRR over labeled queries;
* **impact-pack** precision & recall for callers / tests / config.

A "dataset" is a directory of golden JSON files; each names a fixture repo (relative to the JSON) and
its expected edges/queries/impact. ``run_dataset`` copies each fixture to a temp dir, indexes it, and
scores it — CI-runnable with no Docker or network.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from ..config import Config
from ..core.relationships import RelationType
from ..engine import Engine


# --------------------------------------------------------------------------- call graph
def _real_call_edges(engine: Engine) -> list[tuple[str, str, str]]:
    """(caller_qname, callee_qname, resolution) for CALLS edges to real (non-stub) entities."""
    edges: list[tuple[str, str, str]] = []
    for rel in engine.graph.relationships(RelationType.CALLS):
        src = engine.graph.get_entity(rel.src_id)
        dst = engine.graph.get_entity(rel.dst_id)
        if src is None or dst is None:
            continue
        if dst.attributes.get("missing") or dst.attributes.get("external"):
            continue
        edges.append((src.qualified_name, dst.qualified_name, rel.attributes.get("resolution", "")))
    return edges


def evaluate_callgraph(engine: Engine, golden_calls: list[dict]) -> dict:
    actual = _real_call_edges(engine)
    actual_pairs = {(a, b) for a, b, _ in actual}
    golden_pairs = {(g["from"], g["to"]) for g in golden_calls}
    golden_res = {(g["from"], g["to"]): g.get("resolution") for g in golden_calls}
    tp = actual_pairs & golden_pairs

    by_level: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "n": 0})
    res_match = 0
    for a, b, res in actual:
        level = by_level[res or "unlabeled"]
        level["n"] += 1
        if (a, b) in golden_pairs:
            level["tp"] += 1
            if golden_res.get((a, b)) == res:
                res_match += 1

    per_level = {
        lvl: {"precision": round(d["tp"] / d["n"], 3) if d["n"] else 1.0, "n": d["n"]}
        for lvl, d in sorted(by_level.items())
    }
    return {
        "precision": round(len(tp) / len(actual_pairs), 3) if actual_pairs else 1.0,
        "recall": round(len(tp) / len(golden_pairs), 3) if golden_pairs else 1.0,
        "resolution_accuracy": round(res_match / len(tp), 3) if tp else 1.0,
        "per_level": per_level,
        "counts": {"actual": len(actual_pairs), "golden": len(golden_pairs), "tp": len(tp)},
        "missing": sorted(golden_pairs - actual_pairs)[:10],   # recall misses
        "spurious": sorted(actual_pairs - golden_pairs)[:10],  # precision misses
    }


# --------------------------------------------------------------------------- retrieval
def evaluate_retrieval(engine: Engine, queries: list[dict]) -> dict:
    if not queries:
        return {"queries": 0, "recall@5": 1.0, "recall@10": 1.0, "mrr": 1.0, "details": []}
    r5 = r10 = mrr = 0.0
    details = []
    for query in queries:
        results = engine.search(query["q"], top_k=10)["results"]
        qnames = [r["qualified_name"] for r in results]
        expected = set(query["expected"])
        rank = next((i for i, qn in enumerate(qnames, 1) if qn in expected), 0)
        r5 += 1 if 0 < rank <= 5 else 0
        r10 += 1 if 0 < rank <= 10 else 0
        mrr += (1.0 / rank) if rank else 0.0
        details.append({"q": query["q"], "rank": rank})
    n = len(queries)
    return {
        "queries": n,
        "recall@5": round(r5 / n, 3),
        "recall@10": round(r10 / n, 3),
        "mrr": round(mrr / n, 3),
        "details": details,
    }


# --------------------------------------------------------------------------- impact
def _pr(actual: list[str], golden: list[str]) -> tuple[float, float]:
    a, g = set(actual), set(golden)
    tp = a & g
    precision = len(tp) / len(a) if a else 1.0
    recall = len(tp) / len(g) if g else 1.0
    return precision, recall


def evaluate_impact(engine: Engine, cases: list[dict]) -> dict:
    if not cases:
        return {"cases": 0}
    agg: dict[str, list[tuple[float, float]]] = {"callers": [], "tests": [], "config": []}
    for case in cases:
        imp = engine.impact(case["symbol"])
        if not imp.get("ok"):
            continue
        callers = [h["qualified_name"] for h in imp["callers"]["resolved"] + imp["callers"]["candidates"]]
        tests = [h["qualified_name"] for h in imp["tests"]]
        config = [h["name"] for h in imp["config"]]
        agg["callers"].append(_pr(callers, case.get("callers", [])))
        agg["tests"].append(_pr(tests, case.get("tests", [])))
        agg["config"].append(_pr(config, case.get("config", [])))

    def mean_pr(rows: list[tuple[float, float]]) -> dict:
        if not rows:
            return {"precision": 1.0, "recall": 1.0}
        return {
            "precision": round(sum(p for p, _ in rows) / len(rows), 3),
            "recall": round(sum(r for _, r in rows) / len(rows), 3),
        }

    return {
        "cases": len(cases),
        "callers": mean_pr(agg["callers"]),
        "tests": mean_pr(agg["tests"]),
        "config": mean_pr(agg["config"]),
    }


# --------------------------------------------------------------------------- runner
def evaluate_repo(repo_path: str | Path, golden: dict) -> dict:
    """Index a *copy* of the repo (so no ``.uci`` is written into the fixture) and score it."""
    repo = Path(repo_path)
    tmp = Path(tempfile.mkdtemp())
    dest = tmp / repo.name
    try:
        shutil.copytree(repo, dest)
        engine = Engine(Config.from_env(dest))
        engine.index(full=True)
        result = {
            "callgraph": evaluate_callgraph(engine, golden.get("calls", [])),
            "retrieval": evaluate_retrieval(engine, golden.get("queries", [])),
            "impact": evaluate_impact(engine, golden.get("impact", [])),
        }
        engine.close()
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_dataset(dataset_dir: str | Path) -> dict:
    dataset_dir = Path(dataset_dir)
    fixtures: dict[str, dict] = {}
    for json_file in sorted(dataset_dir.glob("*.json")):
        golden = json.loads(json_file.read_text(encoding="utf-8"))
        repo = (json_file.parent / golden["repo"]).resolve()
        if not repo.exists():
            continue
        fixtures[golden["name"]] = evaluate_repo(repo, golden)
    return {"fixtures": fixtures, "aggregate": _aggregate(fixtures)}


def _aggregate(fixtures: dict[str, dict]) -> dict:
    if not fixtures:
        return {}
    cg = [f["callgraph"] for f in fixtures.values()]
    rt = [f["retrieval"] for f in fixtures.values() if f["retrieval"].get("queries")]
    im = [f["impact"] for f in fixtures.values() if f["impact"].get("cases")]

    def avg(rows, key):
        vals = [r[key] for r in rows]
        return round(sum(vals) / len(vals), 3) if vals else 1.0

    def avg_pr(rows, key):
        return {
            "precision": round(sum(r[key]["precision"] for r in rows) / len(rows), 3),
            "recall": round(sum(r[key]["recall"] for r in rows) / len(rows), 3),
        } if rows else {"precision": 1.0, "recall": 1.0}

    return {
        "callgraph": {
            "precision": avg(cg, "precision"),
            "recall": avg(cg, "recall"),
            "resolution_accuracy": avg(cg, "resolution_accuracy"),
        },
        "retrieval": {
            "recall@5": avg(rt, "recall@5") if rt else 1.0,
            "recall@10": avg(rt, "recall@10") if rt else 1.0,
            "mrr": avg(rt, "mrr") if rt else 1.0,
        },
        "impact": {
            "callers": avg_pr(im, "callers"),
            "tests": avg_pr(im, "tests"),
            "config": avg_pr(im, "config"),
        } if im else {},
    }


__all__ = [
    "evaluate_callgraph",
    "evaluate_retrieval",
    "evaluate_impact",
    "evaluate_repo",
    "run_dataset",
]
