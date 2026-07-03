#!/usr/bin/env python3
"""Flow-eval — scores the flow-level block-scheme builder (Tier-1) for structural correctness.

Builds synthetic graphs (transaction/job/capability anchors) and checks the invariants a correct
flow diagram must satisfy — no dangling edges, every node reachable from the flow's sources, data
and screens are leaves — plus per-fixture golden counts. Deterministic, no toolchain, CI-safe.

Run:  python evals/flow_eval.py     # scorecard + evals/reports/flow-eval-*.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "src"))

from uci.analysis.flow import build_flow, resolve_roots  # noqa: E402
from uci.core import Entity, EntityType, Provenance, Relationship, RelationType  # noqa: E402
from uci.core.ids import relationship_id  # noqa: E402
from uci.graph.inmemory import InMemoryGraphStore  # noqa: E402


def _e(eid, kind, name):
    return Entity(eid, kind, name, name, Provenance("r", "x", 1, 1), {})


def _rel(t, s, d):
    return Relationship(relationship_id(t, s, d), t, s, d, Provenance("r", "x", 1, 1))


def _graph(entities, edges):
    g = InMemoryGraphStore()
    g.add_entities(entities)
    for t, s, d in edges:
        g.add_relationship(_rel(t, s, d))
    return g


def _fx_transaction():
    ents = [_e("t1", EntityType.TRANSACTION_CODE, "TX01"),
            _e("pa", EntityType.LEGACY_PROGRAM, "PROGA"),
            _e("pb", EntityType.LEGACY_PROGRAM, "PROGB"),
            _e("ac", EntityType.DATABASE_TABLE, "ACCT"),
            _e("sc", EntityType.SCREEN, "MENU")]
    edges = [(RelationType.INVOKES, "t1", "pa"), (RelationType.CALLS, "pa", "pb"),
             (RelationType.READS, "pa", "ac"), (RelationType.WRITES, "pb", "ac"),
             (RelationType.USES, "pa", "sc")]
    return _graph(ents, edges), "t1", {"programs": 2, "data": 1, "screens": 1, "nodes": 5}


def _fx_job():
    ents = [_e("j1", EntityType.JCL_JOB, "PAYJOB"),
            _e("p1", EntityType.LEGACY_PROGRAM, "PAYRUN"),
            _e("p2", EntityType.LEGACY_PROGRAM, "PAYFMT"),
            _e("ds", EntityType.DATASET, "PAY.MASTER")]
    edges = [(RelationType.RUNS, "j1", "p1"), (RelationType.CALLS, "p1", "p2"),
             (RelationType.READS, "p1", "ds"), (RelationType.WRITES, "p1", "ds")]
    return _graph(ents, edges), "j1", {"programs": 2, "data": 1, "screens": 0, "nodes": 4}


def _fx_capability():
    ents = [_e("cap", EntityType.BUSINESS_CAPABILITY, "Payments"),
            _e("p1", EntityType.LEGACY_PROGRAM, "PAYAUTH"),
            _e("p2", EntityType.LEGACY_PROGRAM, "PAYRUN"),
            _e("tb", EntityType.DATABASE_TABLE, "LEDGER")]
    edges = [(RelationType.IMPLEMENTS_CAPABILITY, "p1", "cap"),
             (RelationType.IMPLEMENTS_CAPABILITY, "p2", "cap"),
             (RelationType.WRITES, "p1", "tb"), (RelationType.WRITES, "p2", "tb")]
    return _graph(ents, edges), "cap", {"programs": 2, "data": 1, "screens": 0, "nodes": 3}


_FIXTURES = [("transaction", _fx_transaction), ("job", _fx_job), ("capability", _fx_capability)]


def _reachable(starts, adj):
    seen, stack = set(), list(starts)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(adj.get(n, []))
    return seen


def _check(fg, golden) -> list[tuple[str, bool]]:
    ids = {n.id for n in fg.nodes}
    adj: dict[str, list[str]] = {}
    indeg = {n.id: 0 for n in fg.nodes}
    for e in fg.edges:
        adj.setdefault(e.src, []).append(e.dst)
        indeg[e.dst] = indeg.get(e.dst, 0) + 1
    sources = [nid for nid, d in indeg.items() if d == 0]
    has_out = {e.src for e in fg.edges}
    leaf_kinds = {"database_table", "dataset", "screen"}
    checks = [
        ("no dangling edges", all(e.src in ids and e.dst in ids for e in fg.edges)),
        ("all nodes reachable from sources", _reachable(sources, adj) == ids and bool(ids)),
        ("data/screens are leaves", all(n.id not in has_out for n in fg.nodes
                                        if n.kind in leaf_kinds)),
        ("at least one source", bool(sources)),
    ]
    st = fg.stats()
    for key, want in golden.items():
        checks.append((f"golden {key}={want}", st.get(key) == want))
    return checks


def run() -> dict:
    fixtures, total, passed = [], 0, 0
    for name, factory in _FIXTURES:
        graph, anchor_id, golden = factory()
        anchor = graph.get_entity(anchor_id)
        fg = build_flow(graph, resolve_roots(graph, anchor), depth=3)
        checks = _check(fg, golden)
        ok = sum(1 for _, b in checks if b)
        total += len(checks)
        passed += ok
        fixtures.append({"anchor": name, "stats": fg.stats(),
                         "checks_passed": ok, "checks_total": len(checks),
                         "failed": [n for n, b in checks if not b]})
    overall = round(passed / total * 100, 1) if total else 0.0
    return {"run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "overall": overall, "passed": passed, "total": total, "fixtures": fixtures}


def main() -> int:
    report = run()
    print("\nFlow-eval — flow-level block-scheme structural correctness")
    print("=" * 58)
    for f in report["fixtures"]:
        mark = "ok" if not f["failed"] else "FAIL: " + ", ".join(f["failed"])
        print(f"  {f['anchor']:<12} {f['checks_passed']}/{f['checks_total']}  "
              f"{f['stats']['programs']}p {f['stats']['data']}d {f['stats']['nodes']}n  {mark}")
    print(f"\n  overall {report['overall']}  ({report['passed']}/{report['total']} checks)")
    out = EVALS / "reports" / f"flow-eval-{report['run'].replace(':', '').replace('-', '')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"  report: {out.relative_to(EVALS.parent)}")
    return 0 if report["overall"] == 100.0 else 1


if __name__ == "__main__":
    sys.exit(main())
