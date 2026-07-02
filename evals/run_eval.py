#!/usr/bin/env python3
"""UCI evaluation runner.

Executes the golden datasets under evals/datasets/ against the UCI engine and scores
the answers exactly as specified in evals/docs/scoring.md (that document is the contract;
when this file and the doc disagree, the doc wins and this file is fixed).

Usage:
    PYTHONPATH=src python3 evals/run_eval.py                  # all datasets, write report
    PYTHONPATH=src python3 evals/run_eval.py --dataset shop -v
    PYTHONPATH=src python3 evals/run_eval.py --baseline evals/reports/baseline.json
    PYTHONPATH=src python3 evals/run_eval.py --clean          # remove .uci stores afterwards

Only public Engine surfaces are used (same code paths as CLI/MCP/API).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
ROOT = EVALS.parent
sys.path.insert(0, str(ROOT / "src"))

from uci import Config, Engine  # noqa: E402
from uci.core.relationships import RESOLVED_LEVELS  # noqa: E402

WEIGHTS = {
    "calls": 2.0, "copybook_impact": 2.0, "impact": 2.0,
    "jobs": 1.5, "transactions": 1.5, "data_access": 1.5,
    "completeness": 1.5, "gaps": 1.5,
    "symbol_lookup": 1.0, "queries": 1.0,
}


# ---------------------------------------------------------------- helpers
def simple(name: str) -> str:
    return name.rsplit(".", 1)[-1].strip().lower()


def hit_names(results: list[dict]) -> set[str]:
    out = set()
    for r in results:
        out.add(simple(r.get("name") or ""))
        out.add((r.get("qualified_name") or "").lower())
    out.discard("")
    return out


def matches(expected: str, names: set[str], loose: bool = True) -> bool:
    e = expected.lower()
    return e in names or (loose and simple(expected) in names)


def f1(tp: float, fp: float, fn: float) -> float:
    denom = 2 * tp + fp + fn
    if denom == 0:
        return 1.0
    return 2 * tp / denom


def set_f1(golden: set[str], answered: set[str], neutral: set[str] = frozenset()) -> float:
    g = {x.lower() for x in golden}
    a = {x.lower() for x in answered}
    n = {x.lower() for x in neutral}
    tp = len(a & g)
    fp = len(a - g - n)
    fn = len(g - a)
    return f1(tp, fp, fn)


def entity_exists(engine: Engine, name: str) -> bool:
    try:
        return bool(engine.find_symbol(simple(name).upper())["results"]) or \
               bool(engine.find_symbol(name)["results"])
    except Exception:
        return False


def entity_id_of(engine: Engine, name: str) -> str | None:
    for query in (name, simple(name).upper(), simple(name)):
        try:
            res = engine.find_symbol(query)["results"]
        except Exception:
            return None
        if res:
            return res[0]["entity_id"]
    return None


def neighborhood(engine: Engine, name: str) -> tuple[dict[str, dict], list[dict]]:
    eid = entity_id_of(engine, name)
    if eid is None:
        return {}, []
    data = engine.graph_neighborhood(eid, depth=1, limit=500)
    if not data.get("ok"):
        return {}, []
    nodes = {n["id"]: n for n in data["nodes"]}
    return nodes, data["edges"]


# ---------------------------------------------------------------- categories
def run_symbol_lookup(engine, entries, details):
    scores = []
    for e in entries:
        try:
            res = engine.find_symbol(e["name"])["results"] or \
                  engine.find_symbol(simple(e["name"]).upper())["results"]
        except Exception:
            res = []
        got = res[0]["path"] if res else None
        ok = got == e["path"]
        scores.append(1.0 if ok else 0.0)
        if not ok:
            details.append(f"symbol_lookup {e['name']}: expected {e['path']}, got {got}")
    return mean(scores)


def run_calls(engine, entries, details):
    by_from: dict[str, dict] = {}
    for e in entries:
        rec = by_from.setdefault(e["from"], {"internal": {}, "external": set(), "neutral": set()})
        cls = e.get("class", "internal")
        if cls == "internal":
            rec["internal"][e["to"].lower()] = bool(e.get("expect_resolved"))
        elif cls == "external":
            rec["external"].add(e["to"].lower())
        else:
            rec["neutral"].add(e["to"].lower())
    scores = []
    for frm, rec in by_from.items():
        try:
            results = engine.callees(frm, depth=1).get("results", [])
        except Exception:
            results = []
        golden = set(rec["internal"])
        neutral = {simple(x) for x in rec["external"] | rec["neutral"]}
        tp = fp = 0.0
        seen: set[str] = set()
        for r in results:
            keys = {simple(r.get("name") or ""), (r.get("qualified_name") or "").lower()}
            gmatch = next((g for g in golden if g in keys or simple(g) in keys), None)
            if gmatch:
                if gmatch in seen:
                    continue
                seen.add(gmatch)
                needs_resolved = rec["internal"][gmatch]
                resolved = r.get("resolution", "") in RESOLVED_LEVELS
                tp += 1.0 if (resolved or not needs_resolved) else 0.5
            elif keys & neutral:
                if r.get("missing"):
                    fp += 1  # external misclassified as missing artifact
            else:
                fp += 1
        fn = len(golden) - len(seen)
        score = f1(tp, fp, fn)
        scores.append(score)
        if score < 1.0:
            details.append(f"calls {frm}: golden={sorted(golden)} matched={sorted(seen)} fp={fp}")
    return mean(scores)


def copybook_entity_id(engine, name: str) -> str | None:
    """The copybook question is about the .cpy artifact — prefer the COPYBOOK entity even when a
    same-named program exists (Bank-of-Z convention: CREACC.cbl + CREACC.cpy)."""
    try:
        results = engine.find_symbol(name)["results"] or \
                  engine.find_symbol(simple(name).upper())["results"]
    except Exception:
        return None
    for r in results:
        if r.get("kind") == "copybook":
            return r["entity_id"]
    return results[0]["entity_id"] if results else None


def run_copybook_impact(engine, entries, details):
    scores = []
    for e in entries:
        golden = {simple(d) for d in e["dependents"]}
        answered: set[str] = set()
        try:
            imp = engine.impact(e["copybook"])
            if imp.get("ok"):
                for sec in ("resolved", "candidates"):
                    answered |= {simple(h["name"]) for h in imp["callers"].get(sec, [])}
        except Exception:
            pass
        me = copybook_entity_id(engine, e["copybook"])
        nodes, edges = ({}, [])
        if me:
            data = engine.graph_neighborhood(me, depth=1, limit=500)
            if data.get("ok"):
                nodes = {n["id"]: n for n in data["nodes"]}
                edges = data["edges"]
        for edge in edges:
            if edge["target"] == me and edge["source"] in nodes:
                answered.add(simple(nodes[edge["source"]]["name"]))
        answered.discard(simple(e["copybook"]))
        score = set_f1(golden, answered)
        scores.append(score)
        if score < 1.0:
            details.append(f"copybook_impact {e['copybook']}: expected {len(golden)} dependents, "
                           f"matched {len(answered & golden)}, extra {len(answered - golden)}")
    return mean(scores)


def run_jobs(engine, entries, details):
    scores = []
    for e in entries:
        golden = {simple(p) for p in e["programs_internal"]}
        # external utilities AND PROC invocations are neutral (the golden enumerates programs;
        # a RUNS edge to a PROC member is a correct answer the golden doesn't model)
        neutral = {simple(p) for p in e.get("programs_external", []) + e.get("procs", [])}
        nodes, edges = neighborhood(engine, e["job"])
        me = entity_id_of(engine, e["job"])
        answered = {
            simple(nodes[edge["target"]]["name"])
            for edge in edges
            if edge["source"] == me and edge["type"] in ("runs", "calls") and edge["target"] in nodes
        }
        score = set_f1(golden, answered, neutral)
        scores.append(score)
        if score < 1.0:
            details.append(f"jobs {e['job']}: golden={sorted(golden)} answered={sorted(answered)}")
    return mean(scores)


def run_transactions(engine, entries, details):
    scores = []
    for e in entries:
        nodes, edges = neighborhood(engine, e["tran"])
        me = entity_id_of(engine, e["tran"])
        linked = {
            simple(nodes[edge["target"]]["name"])
            for edge in edges
            if edge["source"] == me and edge["type"] in ("invokes", "handles") and edge["target"] in nodes
        }
        ok = simple(e["program"]) in linked
        scores.append(1.0 if ok else 0.0)
        if not ok:
            details.append(f"transactions {e['tran']}: expected {e['program']}, linked={sorted(linked)}")
    return mean(scores)


def run_data_access(engine, entries, details):
    scores = []
    for e in entries:
        try:
            results = engine.find_data_lineage(e["program"]).get("results", [])
        except Exception:
            results = []
        ans = {"reads": set(), "writes": set()}
        for r in results:
            reason = (r.get("reason") or "").lower()
            mode = "reads" if reason.startswith("reads") else "writes" if reason.startswith("writes") else None
            if mode:
                ans[mode].add((r.get("qualified_name") or r.get("name") or "").lower())
        mode_scores = []
        for mode in ("reads", "writes"):
            golden = {t.lower() for t in e.get(mode, [])}
            if not golden:
                continue
            answered = ans[mode]
            other = ans["writes" if mode == "reads" else "reads"]
            tp = fp = 0.0
            matched = set()
            for g in golden:
                if g in answered or simple(g) in {simple(a) for a in answered}:
                    tp += 1.0
                    matched.add(g)
                elif g in other or simple(g) in {simple(a) for a in other}:
                    tp += 0.5  # right table, wrong mode
                    matched.add(g)
            fp = len(answered) - len({a for a in answered if any(
                a == g or simple(g) == simple(a) for g in golden)})
            fn = len(golden) - len(matched)
            mode_scores.append(f1(tp, max(fp, 0), fn))
        score = mean(mode_scores) if mode_scores else 0.0
        scores.append(score)
        if score < 1.0:
            details.append(f"data_access {e['program']}: golden r/w={e.get('reads')}/{e.get('writes')} "
                           f"answered={ {m: sorted(v) for m, v in ans.items()} }")
    return mean(scores)


def run_queries(engine, entries, details):
    scores, rrs, recalls = [], [], []
    for e in entries:
        k = int(e.get("k", 5))
        try:
            results = engine.search(e["q"], top_k=k).get("results", [])
        except Exception:
            results = []
        loose = bool(e.get("loose", True))
        rank = 0
        found = set()
        for i, r in enumerate(results, start=1):
            names = {simple(r.get("name") or ""), (r.get("qualified_name") or "").lower()}
            for exp in e["expected"]:
                if exp.lower() in names or (loose and simple(exp) in names):
                    found.add(exp)
                    if rank == 0:
                        rank = i
        rr = 1.0 / rank if rank else 0.0
        recall = len(found) / len(e["expected"]) if e["expected"] else 1.0
        rrs.append(rr)
        recalls.append(recall)
        score = 0.5 * rr + 0.5 * recall
        scores.append(score)
        if score < 1.0:
            details.append(f"queries '{e['q']}': rank={rank or '-'} found={sorted(found)}")
    return mean(scores), {"mrr": round(mean(rrs), 3), "recall_at_k": round(mean(recalls), 3)}


def run_impact(engine, entries, details):
    scores = []
    for e in entries:
        try:
            imp = engine.impact(e["symbol"])
        except Exception:
            imp = {"ok": False}
        if not imp.get("ok"):
            scores.append(0.0)
            details.append(f"impact {e['symbol']}: not resolved")
            continue
        callers = hit_names(imp["callers"].get("resolved", []) + imp["callers"].get("candidates", []))
        tests = hit_names(imp.get("tests", []))
        config = hit_names(imp.get("config", []))
        parts = []
        for key, answered in (("callers", callers), ("tests", tests), ("config", config)):
            golden = {x.lower() for x in e.get(key, [])}
            gs = {simple(x) for x in golden}
            tp = len({g for g in golden if g in answered or simple(g) in answered})
            fp = len({a for a in answered if a not in golden and a not in gs and "." not in a})
            fn = len(golden) - tp
            parts.append(f1(tp, fp, fn))
        score = mean(parts)
        scores.append(score)
        if score < 1.0:
            details.append(f"impact {e['symbol']}: callers/tests/config F1={['%.2f' % p for p in parts]}")
    return mean(scores)


def run_completeness(engine, entries, details):
    scores = []
    for e in entries:
        try:
            imp = engine.impact(e["symbol"])
        except Exception:
            imp = {"ok": False}
        if not imp.get("ok"):
            scores.append(0.0)
            details.append(f"completeness {e['symbol']}: symbol not in index")
            continue
        claimed_exact = imp.get("completeness", {}).get("level") == "exact"
        if claimed_exact == e["expect_exact"]:
            scores.append(1.0)
        elif claimed_exact and not e["expect_exact"]:
            scores.append(0.0)  # the dangerous lie
            details.append(f"completeness {e['symbol']}: claimed exact but truth is dynamic ({e.get('why')})")
        else:
            scores.append(0.5)  # over-hedging
            details.append(f"completeness {e['symbol']}: over-hedged (truth is static)")
    return mean(scores)


def run_gaps(engine, spec, details):
    try:
        gaps = engine.gaps().get("gaps", [])
    except Exception:
        gaps = []
    names = {simple(g.get("name", "")) for g in gaps}
    must = {simple(x) for x in spec.get("must_report_missing", [])}
    must_not = {simple(x) for x in spec.get("must_not_report_missing", [])}
    # vacuous: nothing required AND the tool reported nothing — an empty index earning a free
    # 1.0 would inflate the track; excluded from the aggregate (scoring.md §2.9)
    if not must and not names:
        return 1.0, True
    recall = len(must & names) / len(must) if must else 1.0
    noise = len(must_not & names) / len(must_not) if must_not else 0.0
    score = recall * (1.0 - noise)
    if score < 1.0:
        details.append(f"gaps: missed={sorted(must - names)} noise={sorted(must_not & names)}")
    return score, False


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


# ---------------------------------------------------------------- mined-derived goldens
def derive_from_mined(mined: dict, categories: list[str]) -> dict:
    out: dict = {}
    if "symbol_lookup" in categories:
        out["symbol_lookup"] = [{"name": n, "path": p} for n, p in sorted(mined["programs"].items())]
    if "calls" in categories:
        entries = []
        # only callers with >=1 internal target: external-only callers would score vacuously
        # (empty golden vs empty answer = 1.0) and inflate an unindexed track (scoring.md §2.2)
        for frm, rec in sorted(mined["calls"].items()):
            if not rec["internal"]:
                continue
            for t in rec["internal"]:
                entries.append({"from": frm, "to": t, "class": "internal"})
            for t in rec["external"]:
                entries.append({"from": frm, "to": t, "class": "external"})
            for t in rec.get("unclassified", []):
                entries.append({"from": frm, "to": t, "class": "neutral"})
        if entries:
            out["calls"] = entries
    if "copybook_impact" in categories:
        out["copybook_impact"] = [
            {"copybook": name, "path": rec["path"], "dependents": rec["dependents"]}
            for name, rec in sorted(mined["copybooks"].items())
            if rec["exists_in_repo"] and rec["dependents"]
        ]
    if "jobs" in categories:
        out["jobs"] = [
            {"job": job, **rec} for job, rec in sorted(mined["jobs"].items())
            if rec["programs_internal"]
        ]
    if "transactions" in categories and mined.get("transactions"):
        out["transactions"] = mined["transactions"]
    if "data_access" in categories:
        out["data_access"] = [
            {"program": prog, **rec} for prog, rec in sorted(mined["data_access"].items())
        ]
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------- coverage
def coverage_of(engine: Engine, golden: dict) -> float:
    wanted: set[str] = set()
    for e in golden.get("symbol_lookup", []):
        wanted.add(e["name"])
    for e in golden.get("copybook_impact", []):
        wanted.add(e["copybook"])
    for e in golden.get("jobs", []):
        wanted.add(e["job"])
    for e in golden.get("data_access", []):
        wanted.add(e["program"])
    if not wanted:
        return 0.0
    present = sum(1 for name in wanted if entity_exists(engine, name))
    return present / len(wanted)


# ---------------------------------------------------------------- orchestration
def run_dataset(ds: dict, verbose: bool) -> dict:
    repo = (EVALS / ds["repo"]).resolve()
    golden = dict(ds.get("categories", {}))
    if ds.get("mined"):
        mined = json.loads((EVALS / "datasets" / ds["mined"]).read_text(encoding="utf-8"))
        for cat, entries in derive_from_mined(mined, ds.get("mined_categories", [])).items():
            golden.setdefault(cat, entries)

    engine = Engine(Config.from_env(repo))
    try:
        stats = engine.index(full=True)
        details: list[str] = []
        cats: dict[str, dict] = {}
        extras: dict[str, dict] = {}
        vacuous: set[str] = set()
        for cat, entries in golden.items():
            if cat == "queries":
                score, extra = run_queries(engine, entries, details)
                extras[cat] = extra
            elif cat == "gaps":
                score, is_vacuous = run_gaps(engine, entries, details)
                if is_vacuous:
                    vacuous.add(cat)
                    extras[cat] = {"vacuous": True}
            else:
                fn = {
                    "symbol_lookup": run_symbol_lookup, "calls": run_calls,
                    "copybook_impact": run_copybook_impact, "jobs": run_jobs,
                    "transactions": run_transactions, "data_access": run_data_access,
                    "impact": run_impact, "completeness": run_completeness,
                }[cat]
                score = fn(engine, entries, details)
            n = len(entries) if isinstance(entries, list) else 1
            cats[cat] = {"score": round(score, 4), "items": n, **extras.get(cat, {})}
        scored = [c for c in cats if c not in vacuous]
        total_w = sum(WEIGHTS[c] for c in scored)
        ds_score = 100.0 * sum(WEIGHTS[c] * cats[c]["score"] for c in scored) / total_w if scored else 0.0
        result = {
            "score": round(ds_score, 1),
            "categories": cats,
            "index_stats": {"entities": stats.entities, "relationships": stats.relationships,
                            "files": stats.files_scanned, "gaps": stats.gaps},
        }
        if ds.get("track") == "mainframe":
            result["coverage"] = round(coverage_of(engine, golden), 3)
        if verbose and details:
            result["failures"] = details[:80]
        return result
    finally:
        engine.close()


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, cwd=ROOT, timeout=5).stdout.strip()
    except Exception:
        return ""


def render_md(report: dict) -> str:
    lines = [f"# UCI Eval Report — {report['run']}", ""]
    for track, tdata in report["tracks"].items():
        lines.append(f"## Track `{track}` — **{tdata['score']:.1f} / 100**")
        header_cats = sorted({c for d in tdata["datasets"].values() for c in d["categories"]})
        lines.append("| dataset | score | " + " | ".join(header_cats) + " | coverage |")
        lines.append("|---" * (len(header_cats) + 3) + "|")
        for name, d in tdata["datasets"].items():
            cells = [f"{d['categories'][c]['score']:.2f}" if c in d["categories"] else "—"
                     for c in header_cats]
            cov = f"{d.get('coverage', '—')}"
            lines.append(f"| {name} | {d['score']:.1f} | " + " | ".join(cells) + f" | {cov} |")
        lines.append("")
        for name, d in tdata["datasets"].items():
            for failure in d.get("failures", []):
                lines.append(f"- `{name}`: {failure}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", help="run a single dataset by name")
    ap.add_argument("--baseline", type=Path, help="compare against a saved report; gate on supported track")
    ap.add_argument("--clean", action="store_true", help="remove .uci stores from eval repos afterwards")
    ap.add_argument("-v", "--verbose", action="store_true", help="include per-item failures in the report")
    args = ap.parse_args()

    datasets = []
    for path in sorted((EVALS / "datasets").glob("*.json")):
        ds = json.loads(path.read_text(encoding="utf-8"))
        if args.dataset and ds.get("name") != args.dataset:
            continue
        if "track" not in ds:
            continue  # mined candidate files etc.
        datasets.append(ds)
    if not datasets:
        print("no datasets matched", file=sys.stderr)
        return 2

    tracks: dict[str, dict] = {}
    for ds in datasets:
        print(f"[eval] {ds['name']} ({ds['track']}) …", flush=True)
        result = run_dataset(ds, verbose=args.verbose)
        tracks.setdefault(ds["track"], {"datasets": {}})["datasets"][ds["name"]] = result
        print(f"        score {result['score']:.1f}  categories " +
              " ".join(f"{c}={v['score']:.2f}" for c, v in result["categories"].items()))
        if args.clean:
            store = (EVALS / ds["repo"] / ".uci").resolve()
            if store.is_dir():
                shutil.rmtree(store)

    for tdata in tracks.values():
        tdata["score"] = round(mean(d["score"] for d in tdata["datasets"].values()), 1)

    report = {
        "run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": git_sha(),
        "tracks": tracks,
    }

    reports_dir = EVALS / "reports"
    reports_dir.mkdir(exist_ok=True)
    stamp = report["run"].replace(":", "").replace("-", "")
    (reports_dir / f"run-{stamp}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (reports_dir / f"run-{stamp}.md").write_text(render_md(report), encoding="utf-8")
    print(f"\nreport: evals/reports/run-{stamp}.json / .md")
    for track, tdata in tracks.items():
        print(f"  {track}: {tdata['score']:.1f}/100")

    if args.baseline and args.baseline.exists():
        base = json.loads(args.baseline.read_text(encoding="utf-8"))
        failed = False
        sup_new = tracks.get("supported", {}).get("score")
        sup_old = base.get("tracks", {}).get("supported", {}).get("score")
        # track-level gate only on full runs — a --dataset subset mean isn't comparable
        if not args.dataset and sup_new is not None and sup_old is not None:
            if sup_new < sup_old - 1.0:
                print(f"REGRESSION: supported track {sup_old} -> {sup_new}")
                failed = True
            for name, d in base["tracks"].get("supported", {}).get("datasets", {}).items():
                new_d = tracks.get("supported", {}).get("datasets", {}).get(name)
                if not new_d:
                    continue
                for cat, val in d["categories"].items():
                    new_val = new_d["categories"].get(cat, {}).get("score", 0.0)
                    if new_val < val["score"] - 0.05:
                        print(f"REGRESSION: {name}/{cat} {val['score']:.2f} -> {new_val:.2f}")
                        failed = True
        mf_new = tracks.get("mainframe", {}).get("score")
        mf_old = base.get("tracks", {}).get("mainframe", {}).get("score")
        if mf_new is not None and mf_old is not None:
            print(f"mainframe track delta: {mf_old} -> {mf_new}")
        if failed:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
