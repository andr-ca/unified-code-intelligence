#!/usr/bin/env python3
"""CFG-eval — scores the control-flow-graph builder for structural correctness (Tier-2 block scheme).

Runs the Python CFG builder over fixtures with known logic and checks the invariants a *correct*
control-flow graph must satisfy — single entry/exit, full reachability both ways, well-formed
decision/loop wiring — plus per-fixture golden counts. Deterministic, no toolchain, CI-safe.

Run:  python evals/cfg_eval.py     # scorecard + evals/reports/cfg-eval-*.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "src"))

from uci.analysis.cfg import build_cobol_cfg, build_python_cfg  # noqa: E402

# (language, source, symbol, golden {decisions, loops, returns}) — each exercises a construct family.
_FIXTURES = [
    ("python", """
def classify(x):
    if x < 0:
        return "neg"
    elif x == 0:
        return "zero"
    else:
        return "pos"
""", "classify", {"decisions": 2, "loops": 0, "returns": 3}),
    ("python", """
def scan(items):
    found = 0
    for it in items:
        if it is None:
            continue
        if it == "stop":
            break
        found += 1
    return found
""", "scan", {"decisions": 2, "loops": 1, "returns": 1}),
    ("python", """
def route(cmd, data):
    match cmd:
        case "add":
            r = data + 1
        case "sub":
            r = data - 1
        case _:
            r = data
    return r
""", "route", {"decisions": 1, "loops": 0, "returns": 1}),
    ("python", """
def guarded(conn):
    try:
        conn.open()
        while conn.busy():
            conn.wait()
    except IOError:
        conn.reset()
    finally:
        conn.close()
    return conn.status
""", "guarded", {"decisions": 0, "loops": 1, "returns": 1}),
    # -- COBOL: IF/ELSE/END-IF, EVALUATE/WHEN, PERFORM UNTIL loop, GO TO, GOBACK, fall-through
    ("cobol", """       IDENTIFICATION DIVISION.
       PROGRAM-ID. POST.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM INIT-PARA.
           IF WS-FLAG = 'Y'
               MOVE 1 TO WS-CODE
           ELSE
               MOVE 0 TO WS-CODE
           END-IF.
           PERFORM CHECK-PARA UNTIL WS-DONE = 'Y'.
           EVALUATE WS-CODE
               WHEN 1
                   DISPLAY 'ONE'
               WHEN OTHER
                   DISPLAY 'OTHER'
           END-EVALUATE.
           GOBACK.
       INIT-PARA.
           MOVE 'N' TO WS-DONE.
       CHECK-PARA.
           IF WS-X > 10
               GO TO DONE-PARA
           END-IF.
           ADD 1 TO WS-X.
       DONE-PARA.
           MOVE 'Y' TO WS-DONE.
""", "POST", {"decisions": 3, "loops": 1, "returns": 1}),
    ("cobol", """       PROGRAM-ID. INQ.
       PROCEDURE DIVISION.
       DRIVER.
           PERFORM VARYING WS-I FROM 1 BY 1 UNTIL WS-I > 10
               ADD WS-I TO WS-TOTAL
               IF WS-TOTAL > WS-LIMIT
                   MOVE 'Y' TO WS-CAP
               END-IF
           END-PERFORM.
           STOP RUN.
""", "INQ", {"decisions": 1, "loops": 1, "returns": 1}),
]


def _adjacency(edges, reverse=False):
    adj: dict[str, list[str]] = {}
    for e in edges:
        a, b = (e.dst, e.src) if reverse else (e.src, e.dst)
        adj.setdefault(a, []).append(b)
    return adj


def _reachable(start: str, adj: dict[str, list[str]]) -> set[str]:
    seen, stack = set(), [start]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(adj.get(n, []))
    return seen


def _check(cfg, golden: dict) -> list[tuple[str, bool]]:
    ids = {n.id for n in cfg.nodes}
    entries = [n for n in cfg.nodes if n.kind == "entry"]
    exits = [n for n in cfg.nodes if n.kind == "exit"]
    checks: list[tuple[str, bool]] = [
        ("single entry", len(entries) == 1),
        ("single exit", len(exits) == 1),
        ("no dangling edges", all(e.src in ids and e.dst in ids for e in cfg.edges)),
    ]
    if entries and exits:
        fwd = _reachable(entries[0].id, _adjacency(cfg.edges))
        bwd = _reachable(exits[0].id, _adjacency(cfg.edges, reverse=True))
        checks.append(("all nodes reachable from entry", fwd == ids))
        checks.append(("all nodes can reach exit", bwd == ids))
    # if-decisions must fork true/false; loops must have a back-edge and an exit edge
    out_labels: dict[str, set[str]] = {}
    for e in cfg.edges:
        out_labels.setdefault(e.src, set()).add(e.label)
    if_ok = all({"true", "false"} <= out_labels.get(n.id, set())
                for n in cfg.nodes if n.kind == "decision" and n.label.lower().startswith("if "))
    loops = [n for n in cfg.nodes if n.kind == "loop"]
    loop_ok = all(
        any(e.dst == ln.id for e in cfg.edges if e.src != ln.id)          # a back-edge into it
        and any(e.src == ln.id and e.label == "exit" for e in cfg.edges)  # and an exit out of it
        for ln in loops)
    checks.append(("if-decisions fork true/false", if_ok))
    checks.append(("loops have back-edge + exit", loop_ok or not loops))
    st = cfg.stats()
    for key, want in golden.items():
        got = st.get(key, st.get("kinds", {}).get(key, 0))
        checks.append((f"golden {key}={want}", got == want))
    return checks


def run() -> dict:
    fixtures = []
    total = passed = 0
    for lang, source, sym, golden in _FIXTURES:
        cfg = (build_python_cfg(source, sym, f"{sym}.py") if lang == "python"
               else build_cobol_cfg(source, sym, f"{sym}.cbl"))
        checks = _check(cfg, golden)
        ok = sum(1 for _, b in checks if b)
        total += len(checks)
        passed += ok
        fixtures.append({"function": sym, "language": lang, "stats": cfg.stats(),
                         "checks_passed": ok, "checks_total": len(checks),
                         "failed": [name for name, b in checks if not b]})
    overall = round(passed / total * 100, 1) if total else 0.0
    return {"run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "overall": overall, "passed": passed, "total": total, "fixtures": fixtures}


def main() -> int:
    report = run()
    print("\nCFG-eval — control-flow-graph structural correctness (Python + COBOL)")
    print("=" * 70)
    for f in report["fixtures"]:
        mark = "ok" if not f["failed"] else "FAIL: " + ", ".join(f["failed"])
        print(f"  {f['function']:<10} {f['language']:<7} {f['checks_passed']}/{f['checks_total']}  "
              f"{f['stats']['decisions']}d {f['stats']['loops']}l {f['stats']['nodes']}n  {mark}")
    print(f"\n  overall {report['overall']}  ({report['passed']}/{report['total']} checks)")
    out = EVALS / "reports" / f"cfg-eval-{report['run'].replace(':', '').replace('-', '')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"  report: {out.relative_to(EVALS.parent)}")
    return 0 if report["overall"] == 100.0 else 1


if __name__ == "__main__":
    sys.exit(main())
