"""``uci`` command-line interface (argparse, standard library only).

Commands: init · index · watch · query · graph symbol · impact · explain · overview ·
architecture · onboarding · serve · mcp.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from ..config import Config
from ..core.entities import EntityType
from ..engine import Engine


def _engine(args) -> Engine:
    overrides = {}
    for key in ("profile", "embedding_provider", "graph_backend", "vector_backend"):
        val = getattr(args, key, None)
        if val:
            overrides[key] = val
    return Engine.open(getattr(args, "path", None) or getattr(args, "repo", None), overrides)


def _ensure_indexed(engine: Engine) -> None:
    if not engine.is_indexed():
        print("Repository not indexed yet. Running `uci index`...", file=sys.stderr)
        engine.index(full=True)


def _print_hits(results: list[dict]) -> None:
    if not results:
        print("  (no results)")
        return
    for r in results:
        loc = f"{r['path']}:{r['start_line']}" if r.get("path") else ""
        signals = ",".join(r.get("signals", []))
        print(f"  {r.get('score', 0):.3f}  {r['kind']:<9} {r['qualified_name']}")
        print(f"         {loc}  [{signals}]  — {r.get('reason', '')}")


# --------------------------------------------------------------------------- commands
def cmd_init(args) -> int:
    cfg = Config.from_env(args.path)
    cfg.store_dir.mkdir(parents=True, exist_ok=True)
    (cfg.store_dir / "config.json").write_text(json.dumps(cfg.to_dict(), indent=2))
    print(f"Initialized UCI store at {cfg.store_dir}")
    print(f"  profile:    {cfg.profile}")
    print(f"  graph:      {cfg.graph_backend}")
    print(f"  vector:     {cfg.vector_backend}")
    print(f"  embeddings: {cfg.embedding_provider} ({cfg.embedding_model})")
    print("Next: uci index")
    return 0


def cmd_index(args) -> int:
    with _engine(args) as engine:
        stats = engine.index(full=args.full)
        if args.json:
            print(json.dumps(stats.to_dict(), indent=2))
        else:
            s = stats.to_dict()
            print(f"Indexed {s['files_scanned']} files ({s['files_changed']} changed) in {s['elapsed_ms']}ms")
            print(f"  entities={s['entities']} relationships={s['relationships']} "
                  f"chunks={s['chunks']} embedded={s['embedded']} commits={s['commits']}")
            if s["errors"]:
                print(f"  {len(s['errors'])} parse warning(s)")
    return 0


def cmd_watch(args) -> int:  # pragma: no cover - long-running loop
    with _engine(args) as engine:
        engine.index(full=False)
        print(f"Watching {engine.config.repo_path} (every {args.interval}s). Ctrl-C to stop.")
        try:
            while True:
                time.sleep(args.interval)
                stats = engine.index(full=False)
                if stats.files_changed:
                    print(f"re-indexed {stats.files_changed} changed file(s) "
                          f"[+{stats.embedded} embedded] at {time.strftime('%H:%M:%S')}")
        except KeyboardInterrupt:
            print("\nstopped.")
    return 0


def cmd_query(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        kinds = [EntityType(k) for k in args.kind] if args.kind else None
        result = engine.search(args.query, top_k=args.k, kinds=kinds)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Query: {args.query}")
            _print_hits(result["results"])
            if result.get("next_queries"):
                print("Next:", "; ".join(result["next_queries"]))
    return 0


def cmd_graph(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        if args.what == "symbol":
            found = engine.find_symbol(args.name, exact=False)
            if args.json:
                print(json.dumps(found, indent=2))
                return 0
            if not found["results"]:
                print(f"No symbol matching {args.name!r}")
                return 1
            target = found["results"][0]
            print(f"{target['kind']} {target['qualified_name']}  {target['path']}:{target['start_line']}")
            callers = engine.callers(target["qualified_name"], depth=args.depth)
            callees = engine.callees(target["qualified_name"], depth=args.depth)
            print(f"\nCallers ({len(callers['results'])}):")
            _print_hits(callers["results"])
            print(f"\nCallees ({len(callees['results'])}):")
            _print_hits(callees["results"])
    return 0


def cmd_impact(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        data = engine.impact(args.target)
        if args.json:
            print(json.dumps(data, indent=2))
            return 0 if data.get("ok") else 1
        if not data.get("ok"):
            print(data.get("error", {}).get("message", "not found"))
            return 1
        t = data["target"]
        risk = data["risk"]
        print(f"Impact: {t['qualified_name']}  ({t['path']}:{t['start_line']})")
        print(f"Risk: {risk['level'].upper()} (score {risk['score']}) — {', '.join(risk['factors'])}")
        comp = data.get("completeness", {})
        print(f"Completeness: {comp.get('level', '?')}" + (f" — {'; '.join(comp['reasons'])}" if comp.get("reasons") else ""))
        callers = data["callers"]
        print(f"\nCallers — resolved ({len(callers['resolved'])}):"); _print_hits(callers["resolved"])
        if callers["candidates"]:
            print(f"Callers — candidates ({len(callers['candidates'])}):"); _print_hits(callers["candidates"])
        if callers["unresolved"]["count"]:
            print(f"  ⚠ {callers['unresolved']['note']}")
        callees = data["callees"]
        print(f"\nCallees — resolved ({len(callees['resolved'])}):"); _print_hits(callees["resolved"])
        if callees["candidates"]:
            print(f"Callees — candidates ({len(callees['candidates'])}):"); _print_hits(callees["candidates"])
        print(f"\nTests ({len(data['tests'])}):"); _print_hits(data["tests"])
        if data["config"]:
            print(f"\nConfig ({len(data['config'])}):"); _print_hits(data["config"])
    return 0


def cmd_explain(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        data = engine.explain_module(args.module)
        print(json.dumps(data, indent=2) if args.json else _fmt_explain(data))
    return 0


def _fmt_explain(data: dict) -> str:
    if not data.get("ok"):
        return data.get("error", {}).get("message", "not found")
    lines = [f"{data['module']}  ({data['path']})", f"  layer: {data['layer']} — {data['purpose']}",
             f"  symbols: {data['symbol_count']}"]
    for s in data["symbols"][:20]:
        lines.append(f"    {s['kind']:<8} {s['name']}")
    return "\n".join(lines)


def cmd_overview(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        data = engine.overview()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            t = data["totals"]
            print(f"{data['name']}  ({data['repo_id']})")
            print(f"  files={t['files']} modules={t['modules']} functions={t['functions']} "
                  f"classes={t['classes']} tests={t['tests']}")
            print(f"  languages: {', '.join(data['languages']) or '—'}")
            print(f"  external:  {', '.join(data['external_dependencies']) or '—'}")
    return 0


def cmd_architecture(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        data = engine.architecture()
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for layer in data["layers"]:
                print(f"{layer['name']} ({layer['module_count']} modules) — {layer['description']}")
    return 0


def cmd_onboarding(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        data = engine.onboarding()
        print(json.dumps(data, indent=2) if args.json else data["markdown"])
    return 0


def cmd_gaps(args) -> int:
    with _engine(args) as engine:
        _ensure_indexed(engine)
        data = engine.gaps(args.kind)
        if args.json:
            print(json.dumps(data, indent=2))
            return 0
        if not data["gaps"]:
            print("No gaps — every referenced artifact is indexed.")
            return 0
        print(f"{data['count']} gap(s) — acquisition checklist (ranked by references):")
        for g in data["gaps"]:
            sites = ", ".join(f"{s['path']}:{s['line']}" for s in g["referencing_sites"][:3])
            print(f"  [{g['artifact_kind']}] {g['name']}  x{g['ref_count']}  expected: {g['expected_origin']}")
            print(f"        reasons: {', '.join(g['reasons'])}  sites: {sites}")
    return 0


def cmd_serve(args) -> int:  # pragma: no cover - I/O
    from ..api.projects import ProjectManager
    from ..api.server import serve

    repo_path = getattr(args, "path", None) or "."
    with _engine(args) as engine:  # ensure the served project is indexed before opening the dashboard
        _ensure_indexed(engine)
    manager = ProjectManager()
    manager.add(str(Path(repo_path).resolve()), activate=True)
    try:
        serve(manager, host=args.host, port=args.port)
    finally:
        manager.close()
    return 0


def cmd_mcp(args) -> int:  # pragma: no cover - I/O
    from ..mcp.server import serve_stdio

    with _engine(args) as engine:
        _ensure_indexed(engine)
        serve_stdio(engine)
    return 0


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="uci", description="Unified Code Intelligence")
    p.add_argument("--profile", help="deployment profile (local-lite/local-pro/cloud)")
    p.add_argument("--embedding-provider", dest="embedding_provider", help="noop/local/ollama/openai")
    p.add_argument("--graph-backend", dest="graph_backend", help="sqlite/memory/memgraph/neo4j")
    p.add_argument("--vector-backend", dest="vector_backend", help="sqlite/qdrant")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="initialize the .uci store"); sp.add_argument("path", nargs="?"); sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("index", help="index a repository")
    sp.add_argument("path", nargs="?"); sp.add_argument("--full", action="store_true")
    sp.add_argument("--json", action="store_true"); sp.set_defaults(func=cmd_index)

    sp = sub.add_parser("watch", help="watch and re-index on change")
    sp.add_argument("path", nargs="?"); sp.add_argument("--interval", type=float, default=2.0)
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("query", help="hybrid code search")
    sp.add_argument("query"); sp.add_argument("-k", type=int, default=10)
    sp.add_argument("--kind", action="append"); sp.add_argument("--json", action="store_true")
    sp.add_argument("--path", help="repository path"); sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("graph", help="inspect the graph")
    sp.add_argument("what", choices=["symbol"]); sp.add_argument("name")
    sp.add_argument("--depth", type=int, default=1); sp.add_argument("--json", action="store_true")
    sp.add_argument("--path"); sp.set_defaults(func=cmd_graph)

    sp = sub.add_parser("impact", help="impact analysis for a symbol or file")
    sp.add_argument("target"); sp.add_argument("--json", action="store_true")
    sp.add_argument("--path"); sp.set_defaults(func=cmd_impact)

    sp = sub.add_parser("explain", help="explain a module/file")
    sp.add_argument("module"); sp.add_argument("--json", action="store_true")
    sp.add_argument("--path"); sp.set_defaults(func=cmd_explain)

    for name, func in (("overview", cmd_overview), ("architecture", cmd_architecture),
                       ("onboarding", cmd_onboarding)):
        sp = sub.add_parser(name, help=f"{name} report")
        sp.add_argument("--json", action="store_true"); sp.add_argument("--path")
        sp.set_defaults(func=func)

    sp = sub.add_parser("gaps", help="list missing artifacts referenced but not indexed")
    sp.add_argument("--kind"); sp.add_argument("--json", action="store_true"); sp.add_argument("--path")
    sp.set_defaults(func=cmd_gaps)

    sp = sub.add_parser("serve", help="run the web dashboard + REST API")
    sp.add_argument("--host", default="127.0.0.1"); sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--path"); sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("mcp", help="run the MCP server over stdio")
    sp.add_argument("--repo", help="repository path"); sp.set_defaults(func=cmd_mcp)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
