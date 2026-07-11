#!/usr/bin/env python3
"""docs-eval — scores the documentation→code linker (DESCRIBES) for precision & recall.

Indexes a repo through the public Engine, then checks the deterministic doc links against a
hand-labeled golden: every ``expected_link`` (a section heading → artifact pair a careful engineer
would assert) must be found (recall), and no ``forbidden_link`` (a plausible false positive like the
word "COBOL") may appear (precision). Deterministic, no toolchain, CI-safe.

Run:  python evals/docs_eval.py           # scorecard + evals/reports/docs-eval-*.json
      python evals/docs_eval.py --json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "src"))

from uci import Config, Engine  # noqa: E402
from uci.core.entities import EntityType  # noqa: E402
from uci.core.relationships import RelationType  # noqa: E402

_GATE_PRECISION = 0.9
_GATE_RECALL = 0.8


def _links_for_doc(engine, doc_path: str) -> list[tuple[str, str]]:
    """All (section_heading, target_name) DESCRIBES links for one document (real targets only)."""
    out: list[tuple[str, str]] = []
    for sec in engine.graph.entities(kind=EntityType.DOC_SECTION, repo_id=engine.repo_id):
        if sec.provenance.path != doc_path:
            continue
        heading = sec.attributes.get("heading", sec.name)
        for rel in engine.graph.out_relationships(sec.id, [RelationType.DESCRIBES]):
            tgt = engine.graph.get_entity(rel.dst_id)
            if tgt is not None and not tgt.attributes.get("missing"):
                out.append((heading, tgt.name))
    return out


def score(engine, dataset: dict) -> dict:
    """Precision/recall of doc links against the golden. Deterministic."""
    expected = dataset.get("expected_links", [])
    forbidden = dataset.get("forbidden_links", [])
    links: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in expected + forbidden:
        dp = item["doc_path"]
        if dp not in links:
            links[dp] = _links_for_doc(engine, dp)

    found, misses = [], []
    for exp in expected:
        pairs = links[exp["doc_path"]]
        hit = any(exp["target"] == name and exp.get("section_contains", "") in heading
                  for heading, name in pairs)
        (found if hit else misses).append(exp["target"])

    forbidden_hits = [f["target"] for f in forbidden
                      if any(f["target"] == name for _, name in links[f["doc_path"]])]

    recall = len(found) / len(expected) if expected else 1.0
    denom = len(found) + len(forbidden_hits)
    precision = len(found) / denom if denom else 1.0
    gate = "PASS" if (precision >= _GATE_PRECISION and recall >= _GATE_RECALL) else "FAIL"
    return {
        "name": dataset.get("name", "doc-links"),
        "precision": round(precision, 3), "recall": round(recall, 3), "gate": gate,
        "found": len(found), "expected": len(expected),
        "misses": misses, "forbidden_hits": forbidden_hits,
    }


def _scorecard(result: dict) -> str:
    lines = [
        f"docs-eval · {result['name']}",
        f"  recall    : {result['recall']:.2f}  ({result['found']}/{result['expected']} expected links found)",
        f"  precision : {result['precision']:.2f}  ({len(result['forbidden_hits'])} forbidden link(s) present)",
        f"  gate      : {result['gate']}  (need precision >= {_GATE_PRECISION}, recall >= {_GATE_RECALL})",
    ]
    if result["misses"]:
        lines.append(f"  misses    : {', '.join(result['misses'])}")
    if result["forbidden_hits"]:
        lines.append(f"  FALSE +   : {', '.join(result['forbidden_hits'])}")
    return "\n".join(lines)


def run_dataset(dataset_path: Path) -> dict:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    repo = (EVALS.parent / dataset["repo"]).resolve()
    with Engine(Config.from_env(repo)) as eng:
        eng.index(full=True)
        return score(eng, dataset)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Score the documentation→code linker.")
    ap.add_argument("--dataset", default=str(EVALS / "datasets" / "doc_links" / "carddemo.json"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    result = run_dataset(Path(args.dataset))
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = EVALS / "reports" / f"docs-eval-{ts}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2) if args.json else _scorecard(result))
    return 0 if result["gate"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
