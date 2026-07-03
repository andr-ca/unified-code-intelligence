#!/usr/bin/env python3
"""LSP-eval — scores the edge-oracle bridge (verify / discover / complete) against golden fixtures.

Unlike `llm_eval.py` (model ability), this measures the *harness*: given a language server's answers,
does UCI promote the right edges, prune the false ones, leave the uncertain ones alone, discover the
missed ones, and complete references correctly? It drives the **real** transport — `LspClient` spawns
`evals/tools/scripted_lsp.py` as a subprocess and talks genuine LSP over stdio — so it exercises the
whole stack deterministically and in CI, with no language server installed.

Run:  python evals/lsp_eval.py          # prints a scorecard, writes evals/reports/lsp-eval-*.json
"""

from __future__ import annotations

import json
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "src"))

from uci.core import Entity, EntityType, Provenance, Relationship, RelationType  # noqa: E402
from uci.core.ids import relationship_id  # noqa: E402
from uci.enrich.lsp_source import LspEdgeSource  # noqa: E402
from uci.graph.inmemory import InMemoryGraphStore  # noqa: E402

SCRIPTED = EVALS / "tools" / "scripted_lsp.py"

_MAIN_SRC = (
    "       PROGRAM-ID. MAIN.\n"           # 1
    "       PROCEDURE DIVISION.\n"         # 2
    "           CALL 'HELPER'.\n"          # 3  speculative MAIN->HELPER  (golden: promote)
    "           CALL 'OTHER'.\n"           # 4  speculative MAIN->OTHER   (golden: prune → POSTER)
    "           CALL WS-DYNAMIC.\n"        # 5  unresolved site           (golden: discover → POSTER)
    "           CALL 'GHOST'.\n")          # 6  speculative MAIN->GHOST   (golden: leave, server unsure)
_A_SRC = "       PROGRAM-ID. A.\n           CALL 'HELPER'.\n"  # references HELPER at line 2


def _prog(eid: str, name: str, path: str) -> Entity:
    return Entity(eid, EntityType.LEGACY_PROGRAM, name, name, Provenance("r", path, 1, 1), {})


def _spec_edge(src: Entity, dst: Entity, line: int) -> Relationship:
    return Relationship(
        id=relationship_id(RelationType.CALLS, src.id, dst.id, ordinal=line),
        type=RelationType.CALLS, src_id=src.id, dst_id=dst.id,
        provenance=Provenance("r", "cbl/MAIN.cbl", line, line, "cobol_parser", 0.5),
        attributes={"resolution": "name-match"})


def _build_fixture(root: Path) -> dict:
    cbl = root / "cbl"
    cbl.mkdir(parents=True, exist_ok=True)
    (cbl / "MAIN.cbl").write_text(_MAIN_SRC, encoding="utf-8")
    (cbl / "A.cbl").write_text(_A_SRC, encoding="utf-8")
    for name in ("HELPER", "OTHER", "POSTER", "GHOST"):
        (cbl / f"{name}.cbl").write_text(f"       PROGRAM-ID. {name}.\n", encoding="utf-8")

    graph = InMemoryGraphStore()
    ents = {n: _prog(f"p:{n.lower()}", n, f"cbl/{n}.cbl")
            for n in ("MAIN", "HELPER", "OTHER", "POSTER", "GHOST", "A")}
    graph.add_entities(list(ents.values()))
    e1 = _spec_edge(ents["MAIN"], ents["HELPER"], 3)
    e2 = _spec_edge(ents["MAIN"], ents["OTHER"], 4)
    e3 = _spec_edge(ents["MAIN"], ents["GHOST"], 6)
    for e in (e1, e2, e3):
        graph.add_relationship(e)

    worklist = [{"path": "cbl/MAIN.cbl", "line": 5, "name": "WS-DYNAMIC", "caller": "MAIN"}]
    responses = {
        "definitions": [
            {"file": "MAIN.cbl", "line": 2, "target": "cbl/HELPER.cbl", "target_line": 0},  # promote e1
            {"file": "MAIN.cbl", "line": 3, "target": "cbl/POSTER.cbl", "target_line": 0},  # prune e2
            {"file": "MAIN.cbl", "line": 4, "target": "cbl/POSTER.cbl", "target_line": 0},  # discover
            # line 5 (GHOST) intentionally absent → server returns null → e3 left alone
        ],
        "references": [
            {"file": "HELPER.cbl", "line": 0,
             "targets": [{"file": "cbl/MAIN.cbl", "line": 2}, {"file": "cbl/A.cbl", "line": 1}]},
        ],
    }
    (root / "responses.json").write_text(json.dumps(responses), encoding="utf-8")
    golden = {
        "promote": {e1.id}, "prune": {e2.id}, "leave": {e3.id},
        "discover": {(ents["MAIN"].id, ents["POSTER"].id)},
        "complete": {(ents["MAIN"].id, ents["HELPER"].id), (ents["A"].id, ents["HELPER"].id)},
    }
    return {"graph": graph, "worklist": worklist, "symbols": [ents["HELPER"]], "golden": golden}


def _prf(predicted: set, golden: set) -> dict:
    tp = len(predicted & golden)
    precision = tp / len(predicted) if predicted else (1.0 if not golden else 0.0)
    recall = tp / len(golden) if golden else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "tp": tp, "predicted": len(predicted), "golden": len(golden)}


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="uci-lsp-eval-") as tmp:
        root = Path(tmp)
        fx = _build_fixture(root)
        graph, golden = fx["graph"], fx["golden"]
        cmd = " ".join(shlex.quote(p) for p in
                       [sys.executable, str(SCRIPTED), str(root / "responses.json"), str(root)])
        src = LspEdgeSource("cobol", str(root), settings={"lsp_cobol_cmd": cmd})
        if not src.available:
            raise RuntimeError("scripted LSP server did not resolve — check evals/tools/scripted_lsp.py")
        try:
            vd = src.verify(graph, "r")
            dd = src.discover(graph, "r", fx["worklist"])
            cd = src.complete(graph, "r", fx["symbols"])
        finally:
            src.close()

    promoted = {r.id for r in vd.promoted}
    pruned = {r.id for r in vd.pruned}
    discovered = {(r.src_id, r.dst_id) for r in dd.discovered}
    completed = {(r.src_id, r.dst_id) for r in cd.discovered}
    # "leave" is correct iff the uncertain edge was neither promoted nor pruned
    left_ok = golden["leave"].isdisjoint(promoted | pruned)

    scores = {
        "promote": _prf(promoted, golden["promote"]),
        "prune": _prf(pruned, golden["prune"]),
        "discover": _prf(discovered, golden["discover"]),
        "complete": _prf(completed, golden["complete"]),
    }
    f1s = [s["f1"] for s in scores.values()] + [1.0 if left_ok else 0.0]
    overall = round(sum(f1s) / len(f1s) * 100, 1)
    return {"run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "overall": overall, "left_uncertain_ok": left_ok, "scores": scores,
            "queried": {"verify": vd.queried, "discover": dd.queried, "complete": cd.queried}}


def main() -> int:
    report = run()
    print("\nLSP-eval — edge-oracle bridge (scripted server, real transport)")
    print("=" * 66)
    for mode, s in report["scores"].items():
        print(f"  {mode:<9} P {s['precision']:.2f}  R {s['recall']:.2f}  F1 {s['f1']:.2f}  "
              f"({s['tp']}/{s['predicted']} pred, {s['golden']} golden)")
    print(f"  {'leave':<9} {'ok' if report['left_uncertain_ok'] else 'FAILED'} "
          f"(uncertain edge untouched)")
    print(f"\n  overall {report['overall']}   queries {report['queried']}")
    out = EVALS / "reports" / f"lsp-eval-{report['run'].replace(':', '').replace('-', '')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"  report: {out.relative_to(EVALS.parent)}")
    return 0 if report["overall"] == 100.0 else 1


if __name__ == "__main__":
    sys.exit(main())
