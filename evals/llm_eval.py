#!/usr/bin/env python3
"""LLM-eval — scores LLM models on UCI's enrichment task areas (docs/llm-eval.md).

Separate from the main eval (run_eval.py): the main eval scores the *system* with guardrails
applied; LLM-eval scores the *model's raw ability* on the production prompts, so you can pick a
model per deployment (and catch failure modes like thinking models returning empty content).

Usage (three ways to run):
    python3 evals/llm_eval.py --interactive                  # step-by-step interactive mode (easiest)
    python3 evals/llm_eval.py --models frontier --tools      # direct flags (fast)
    python3 evals/llm_eval.py --list                         # show the model/scope menu

Examples:
    python3 evals/llm_eval.py --models local --scope smoke   # quick local sanity
    python3 evals/llm_eval.py --models qwen-coder,gemma4b --tools  # mix frontier + local
    python3 evals/llm_eval.py --models freellm:gpt-4.1       # raw protocol:model syntax

`--models` takes aliases (`qwen-coder`), groups (`local|frontier|all`), raw `protocol:model`, or
bare names (on `--protocol`, default ollama). `--scope smoke|full` sizes the task set; `--tools`
adds the agentic tool-loop tasks. Edit the MODELS / GROUPS / SCOPES tables below to change the menu.

Uses the SAME system prompts as production (imported from uci.enrich.enricher) against golden
fixtures with known answers. No repo indexing needed — pure prompt->response scoring.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS.parent / "src"))

from uci.config import Config  # noqa: E402
from uci.enrich.enricher import (  # noqa: E402 - production prompts, by design
    _SYS_CANDIDATES, _SYS_CAPABILITIES, _SYS_FIELDS, _SYS_SUMMARY,
)
from uci.enrich.llm_client import LlmClient, LlmError  # noqa: E402
from uci.enrich.tool_loop import ToolLoop  # noqa: E402

TASKS_VERSION = 2  # v2: hardened _SYS_CANDIDATES + agentic tasks (docs/agentic-enrichment.md §6)

_ASK_SYS = (
    "You route a question about a codebase to where its answer lives. Reply with STRICT JSON "
    "only: {\"answer_location\": \"code\"|\"data\"|\"not_in_repo\", \"targets\": "
    "[{\"name\": str, \"kind\": str, \"why\": str}], \"explanation\": str, \"next_step\": str}. "
    "Questions about configured/stored values (product lists, rates, codes) are usually "
    "DATA-resident: name the table/dataset to query. Use ONLY names from the provided context."
)

_PRODINQ_SRC = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PRODINQ.
      * product inquiry: look up supported products from the catalog
       PROCEDURE DIVISION.
           EXEC SQL SELECT PROD_NAME FROM SHOP.PRODUCT_CATALOG
                    WHERE PROD_ID = :WS-ID END-EXEC.
           CALL 'PRODFMT' USING WS-REC.
           GOBACK.
"""

_ROUTER_SRC = """\
       01  MENU-TABLE.
           05 FILLER PIC X(8) VALUE 'PGMA'.
           05 FILLER PIC X(8) VALUE 'PGMB'.
       01  MENU-PGM REDEFINES MENU-TABLE OCCURS 2 PIC X(8).
       01  WS-DISPATCH PIC X(8).
       PROCEDURE DIVISION.
           MOVE MENU-PGM(WS-IDX) TO WS-DISPATCH.
           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC.
"""

_OPAQUE_SRC = """\
       01  WS-DISPATCH PIC X(8).
       PROCEDURE DIVISION.
           MOVE LK-NEXT-PGM TO WS-DISPATCH.
           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC.
"""

_DCLGEN_SRC = """\
           EXEC SQL DECLARE SHOP.PRODUCT_CATALOG TABLE
           ( PROD_ID     CHAR(8) NOT NULL,
             PROD_NAME   VARCHAR(40),
             UNIT_PRICE  DECIMAL(9,2)
           ) END-EXEC.
       01  DCLPRODCAT.
           10 PROD-ID     PIC X(8).
           10 PROD-NAME   PIC X(40).
           10 UNIT-PRICE  PIC S9(7)V99 COMP-3.
"""

_CAP_INVENTORY = """\
- PAYRUN: Executes the nightly payment settlement batch against the payments ledger.
- PAYAUTH: Authorizes individual payment transactions online.
- STMTGEN: Generates monthly customer account statements.
- STMTPRT: Formats and prints statement output files.
- PRODINQ: Looks up supported products from the product catalog table.
- PRODFMT: Formats product records for display.
"""
_CAP_PROGRAMS = {"PAYRUN", "PAYAUTH", "STMTGEN", "STMTPRT", "PRODINQ", "PRODFMT"}

_ASK_CONTEXT = """\
Code matches:
- PRODINQ (legacy_program): Looks up supported products from the catalog
- PRODFMT (legacy_program): Formats product records for display

Data inventory (tables/datasets with reader/writer programs):
[{"name": "SHOP.PRODUCT_CATALOG", "kind": "database_table", "read_by": ["PRODINQ"],
  "written_by": ["PRODLOAD"], "fields": ["PROD-ID", "PROD-NAME", "UNIT-PRICE"]},
 {"name": "SHOP.AUDITLOG", "kind": "database_table", "read_by": [], "written_by": ["PAYRUN"],
  "fields": []}]
"""


# ---------------------------------------------------------------- task scoring
def score_summary(client: LlmClient, name: str, source: str, keywords: list[str]) -> tuple[float, str]:
    text = client.complete(
        _SYS_SUMMARY,
        f"Artifact: {name} (kind=legacy_program, language=cobol)\n"
        f"Structural facts:\n(none)\n\nSource (head):\n{source}",
        max_tokens=220,
    ).strip()
    if not text:
        return 0.0, "empty response"
    hits = sum(1 for k in keywords if k.lower() in text.lower())
    brevity = 1.0 if len(text) <= 400 else 0.5
    score = (hits / len(keywords)) * 0.7 + brevity * 0.3
    return round(score, 3), text[:120]


def score_capabilities(client: LlmClient) -> tuple[float, str]:
    try:
        data = client.complete_json(_SYS_CAPABILITIES, f"Program inventory:\n{_CAP_INVENTORY}",
                                    max_tokens=900)
    except LlmError as exc:
        return 0.0, f"invalid JSON: {exc}"
    if not isinstance(data, list) or not data:
        return 0.0, "not a list"
    assigned, hallucinated = set(), 0
    for cap in data:
        for p in cap.get("programs", []):
            (assigned.add(p) if p in _CAP_PROGRAMS else None)
            hallucinated += p not in _CAP_PROGRAMS
    coverage = len(assigned) / len(_CAP_PROGRAMS)
    total_refs = sum(len(c.get("programs", [])) for c in data) or 1
    honesty = 1.0 - hallucinated / total_refs
    sane_count = 1.0 if 2 <= len(data) <= 5 else 0.5
    score = coverage * 0.4 + honesty * 0.4 + sane_count * 0.2
    return round(score, 3), f"{len(data)} caps, coverage {coverage:.0%}, hallucinated {hallucinated}"


def score_candidates(client: LlmClient, source: str, inventory: list[str],
                     golden: set[str]) -> tuple[float, str]:
    try:
        data = client.complete_json(
            _SYS_CANDIDATES,
            f"Dynamic call through variable WS-DISPATCH at demo.cbl:8.\n"
            f"Program inventory: {', '.join(inventory)}\n\nSource context:\n{source}",
            max_tokens=200,
        )
    except LlmError as exc:
        return 0.0, f"invalid JSON: {exc}"
    got = {str(c).upper() for c in (data or {}).get("candidates", [])}
    if not golden:
        return (1.0, "correctly abstained") if not got else (0.0, f"hallucinated {sorted(got)}")
    tp = len(got & golden)
    denom = 2 * tp + len(got - golden) + len(golden - got)
    f1 = 2 * tp / denom if denom else 1.0
    return round(f1, 3), f"got {sorted(got)} vs golden {sorted(golden)}"


def score_fields(client: LlmClient) -> tuple[float, str]:
    try:
        data = client.complete_json(_SYS_FIELDS, f"Copybook DCLPROD:\n{_DCLGEN_SRC}", max_tokens=600)
    except LlmError as exc:
        return 0.0, f"invalid JSON: {exc}"
    fields = {str(f.get("name", "")).upper().replace("_", "-"): str(f.get("meaning", ""))
              for f in (data or {}).get("fields", [])}
    expected = {"PROD-ID", "PROD-NAME", "UNIT-PRICE"}
    covered = sum(1 for e in expected if e in fields and len(fields[e]) > 3)
    return round(covered / len(expected), 3), f"covered {covered}/{len(expected)}"


# -- agentic tasks: the deciding context is in a file the seed window does NOT show ----------
# A dispatch table lives in a copybook; the LINKAGE case is a separate file. The tool-loop must
# pull the right file to answer/abstain — one-shot literally cannot (docs/agentic-enrichment.md §6).
_DISPTBL_CPY = """\
      * dispatch table for the menu router
       01  MENU-TABLE.
           05 FILLER PIC X(8) VALUE 'ACCTVIEW'.
           05 FILLER PIC X(8) VALUE 'ACCTEDIT'.
       01  MENU-PGM REDEFINES MENU-TABLE OCCURS 2 PIC X(8).
"""
_ROUTER_THIN = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. MROUTER.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           COPY DISPTBL.
       01  WS-DISPATCH PIC X(8).
       PROCEDURE DIVISION.
           MOVE MENU-PGM(WS-IDX) TO WS-DISPATCH.
           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC.
           GOBACK.
"""
_LINKAGE_PGM = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. LROUTER.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-DISPATCH PIC X(8).
       LINKAGE SECTION.
       01  LK-NEXT-PGM PIC X(8).
       PROCEDURE DIVISION USING LK-NEXT-PGM.
           MOVE LK-NEXT-PGM TO WS-DISPATCH.
           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC.
           GOBACK.
"""


def _agentic_repo(tmp: Path):
    """Index a tiny repo so the tool-loop's read-only surfaces work exactly as in production."""
    from uci import Config, Engine
    (tmp / "cbl").mkdir(parents=True, exist_ok=True)
    (tmp / "cpy").mkdir(parents=True, exist_ok=True)
    (tmp / "cbl" / "MROUTER.cbl").write_text(_ROUTER_THIN, encoding="utf-8")
    (tmp / "cbl" / "LROUTER.cbl").write_text(_LINKAGE_PGM, encoding="utf-8")
    (tmp / "cbl" / "ACCTVIEW.cbl").write_text("       PROGRAM-ID. ACCTVIEW.\n", encoding="utf-8")
    (tmp / "cbl" / "ACCTEDIT.cbl").write_text("       PROGRAM-ID. ACCTEDIT.\n", encoding="utf-8")
    (tmp / "cpy" / "DISPTBL.cpy").write_text(_DISPTBL_CPY, encoding="utf-8")
    eng = Engine(Config.from_env(tmp, {"embedding_provider": "noop"}))
    eng.index(full=True)
    return eng


def _run_loop(client, eng, program, var, golden, source_hint):
    # give the loop the same discovery surfaces production `ask` gets: rag_search + list_files let
    # the model locate the copybook that holds the dispatch table (docs/agentic-enrichment.md §3).
    loop = ToolLoop(client, eng.graph, eng.config.repo_path, eng.repo_id,
                    retriever=eng._retriever(), metadata=eng.metadata, max_tool_calls=4)
    inventory = "ACCTVIEW, ACCTEDIT, PRODINQ, PAYRUN"
    user = (f"Dynamic call through variable {var} in program {program} "
            f"(source file cbl/{program}.cbl).\nProgram inventory: {inventory}\n\n"
            f"Source context:\n{source_hint}")
    result = loop.run(_SYS_CANDIDATES, user, answer_key="candidates", max_tokens=400)
    got = {str(c).upper() for c in (result.answer or {}).get("candidates", [])}
    disc = 1.0 if (result.tool_calls <= 4 and result.protocol_errors == 0) else 0.5
    if not golden:
        base = 1.0 if not got else 0.0
        note = f"got {sorted(got)} (calls={result.tool_calls}, want abstain)"
    else:
        tp = len(got & golden)
        denom = 2 * tp + len(got - golden) + len(golden - got)
        base = 2 * tp / denom if denom else 1.0
        note = f"got {sorted(got)} vs {sorted(golden)} (calls={result.tool_calls})"
    return round(base * 0.8 + disc * 0.2, 3), note


def score_agentic_cross_file(client, eng):
    src = "           MOVE MENU-PGM(WS-IDX) TO WS-DISPATCH.\n           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC."
    return _run_loop(client, eng, "MROUTER", "WS-DISPATCH", {"ACCTVIEW", "ACCTEDIT"}, src)


def score_agentic_restraint(client, eng):
    src = "           MOVE LK-NEXT-PGM TO WS-DISPATCH.\n           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC."
    return _run_loop(client, eng, "LROUTER", "WS-DISPATCH", set(), src)


def score_ask(client: LlmClient, question: str, want_location: str,
              want_target: str | None) -> tuple[float, str]:
    try:
        data = client.complete_json(_ASK_SYS, f"Question: {question}\n\n{_ASK_CONTEXT}",
                                    max_tokens=400)
    except LlmError as exc:
        return 0.0, f"invalid JSON: {exc}"
    if not isinstance(data, dict):
        return 0.0, f"expected JSON object, got {type(data).__name__}"
    location = data.get("answer_location", "")
    targets = [str(t.get("name", "")) for t in data.get("targets", []) if isinstance(t, dict)]
    loc_ok = location == want_location
    tgt_ok = want_target is None or any(want_target in t for t in targets)
    score = (0.6 if loc_ok else 0.0) + (0.4 if tgt_ok else 0.0)
    return round(score, 3), f"location={location} targets={targets[:3]}"


# ---------------------------------------------------------------- harness
TASKS = [
    ("summaries", "summary_prodinq",
     lambda c: score_summary(c, "PRODINQ", _PRODINQ_SRC, ["product", "catalog", "look"])),
    ("summaries", "summary_router",
     lambda c: score_summary(c, "ROUTER", _ROUTER_SRC, ["dispatch", "menu", "transfer"])),
    ("capabilities", "capability_grouping", score_capabilities),
    ("candidates", "candidates_from_value_table",
     lambda c: score_candidates(c, _ROUTER_SRC, ["PGMA", "PGMB", "PGMC", "OTHER"], {"PGMA", "PGMB"})),
    ("candidates", "candidates_restraint_when_opaque",
     lambda c: score_candidates(c, _OPAQUE_SRC, ["PGMA", "PGMB", "PGMC", "OTHER"], set())),
    ("fields", "dclgen_dictionary", score_fields),
    ("ask", "ask_data_resident",
     lambda c: score_ask(c, "what products are supported by the app?", "data", "PRODUCT_CATALOG")),
    ("ask", "ask_code_resident",
     lambda c: score_ask(c, "which program formats product records for display?", "code", "PRODFMT")),
]

# agentic tasks receive (client, engine); the deciding context is in a file outside the seed window
AGENTIC_TASKS = [
    ("agentic", "agentic_cross_file_resolution", score_agentic_cross_file),
    ("agentic", "agentic_restraint", score_agentic_restraint),
]

# ---------------------------------------------------------------- model presets & scopes
_PROTOCOLS = ("ollama", "openai", "anthropic", "freellm")


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    protocol: str
    model: str
    url: str = ""

    @property
    def label(self) -> str:
        return self.model or self.alias


#: short aliases → (protocol, model). Edit here to add a model to the menu.
MODELS: dict[str, ModelSpec] = {
    # local Ollama (keyless)
    "qwen4b":      ModelSpec("qwen4b", "ollama", "qwen3.5:4b"),
    "qwen2b":      ModelSpec("qwen2b", "ollama", "qwen3.5:2b"),
    "gemma4b":     ModelSpec("gemma4b", "ollama", "gemma4:e4b"),
    # free-frontier via the local freellm gateway (key in .env)
    "qwen-coder":  ModelSpec("qwen-coder", "freellm", "qwen3-coder-480b"),
    "gpt-4.1":     ModelSpec("gpt-4.1", "freellm", "gpt-4.1"),
    "gemini-lite": ModelSpec("gemini-lite", "freellm", "gemini-2.5-flash-lite"),
}

#: named bundles so `--models local` / `frontier` / `all` just work.
GROUPS: dict[str, list[str]] = {
    "local":    ["qwen4b", "gemma4b"],
    "frontier": ["qwen-coder", "gpt-4.1"],
    "all":      ["qwen4b", "gemma4b", "qwen-coder", "gpt-4.1"],
}

#: scope → the set of task ids to run (None = every task). Smaller scope = faster sanity check.
SCOPES: dict[str, set[str] | None] = {
    # fast, high-signal subset: one summary, the safety-critical restraint task, one routing task,
    # plus both agentic tasks (only run when --tools is on).
    "smoke": {"summary_prodinq", "candidates_restraint_when_opaque", "ask_data_resident",
              "agentic_cross_file_resolution", "agentic_restraint"},
    "full": None,
}


def resolve_models(spec: str, default_protocol: str = "ollama") -> list[ModelSpec]:
    """Turn a --models string into ModelSpecs. Accepts aliases (`qwen-coder`), groups (`frontier`),
    raw `protocol:model` (`freellm:gpt-4.1`), or a bare model name (uses ``default_protocol``)."""
    out: list[ModelSpec] = []
    seen: set[str] = set()
    for tok in (t.strip() for t in spec.split(",")):
        if not tok:
            continue
        specs: list[ModelSpec]
        if tok in GROUPS:
            specs = [MODELS[a] for a in GROUPS[tok]]
        elif tok in MODELS:
            specs = [MODELS[tok]]
        elif ":" in tok and tok.split(":", 1)[0] in _PROTOCOLS:
            proto, model = tok.split(":", 1)
            specs = [ModelSpec(tok, proto, model)]
        else:  # bare model name on the default protocol (back-compat with --protocol)
            specs = [ModelSpec(tok, default_protocol, tok)]
        for s in specs:
            key = f"{s.protocol}:{s.model}"
            if key not in seen:
                seen.add(key)
                out.append(s)
    return out


def select_tasks(scope: str, tools: bool, agentic_engine=None) -> list[tuple]:
    """Build the (area, task_id, fn) list for the chosen scope, adding agentic tasks when tools=on."""
    keep = SCOPES.get(scope, None)
    tasks = [t for t in TASKS if keep is None or t[1] in keep]
    if tools and agentic_engine is not None:
        tasks += [(a, tid, lambda c, fn=fn: fn(c, agentic_engine))
                  for a, tid, fn in AGENTIC_TASKS if keep is None or tid in keep]
    return tasks


def evaluate_model(spec: ModelSpec, timeout: int, tasks: list[tuple],
                   log_path: str = "") -> dict:
    cfg = Config.from_env(overrides={
        "llm_protocol": spec.protocol, "llm_url": spec.url, "llm_model": spec.model,
        "llm_timeout": timeout, "llm_log": log_path,
    })
    client = LlmClient(cfg)
    areas: dict[str, list[float]] = {}
    details = []
    t0 = time.perf_counter()
    for area, task_id, fn in tasks:
        client.default_tag = f"{spec.label}:{task_id}"  # attribute every logged call to this task
        started = time.perf_counter()
        try:
            score, note = fn(client)
        except LlmError as exc:
            score, note = 0.0, f"error: {exc}"
        elapsed = round(time.perf_counter() - started, 1)
        areas.setdefault(area, []).append(score)
        details.append({"task": task_id, "area": area, "score": score,
                        "note": note, "seconds": elapsed})
        print(f"    {task_id:<34} {score:>5.2f}  ({elapsed}s)  {note[:76]}")
    area_scores = {a: round(sum(v) / len(v), 3) for a, v in areas.items()}
    overall = round(sum(area_scores.values()) / len(area_scores) * 100, 1) if area_scores else 0.0
    return {"model": spec.label, "protocol": spec.protocol, "overall": overall,
            "areas": area_scores, "tasks": details,
            "total_seconds": round(time.perf_counter() - t0, 1)}


def _print_menu() -> None:
    print("Model aliases (use with --models):")
    for alias, s in MODELS.items():
        print(f"  {alias:<13} {s.protocol}/{s.model}")
    print("\nGroups:")
    for g, members in GROUPS.items():
        print(f"  {g:<13} {', '.join(members)}")
    print("\nScopes (--scope):  smoke = fast subset,  full = every task (default)")
    print("Also accepted: raw  protocol:model  (e.g. freellm:gpt-4.1) or a bare model name.")
    print("\nExamples:")
    print("  llm_eval.py --models frontier --tools           # qwen-coder + gpt-4.1, with tools")
    print("  llm_eval.py --models local --scope smoke         # quick local sanity, no tools")
    print("  llm_eval.py --models qwen-coder,gemma4b --tools  # mix a frontier + a local model")


def _interactive() -> tuple[str, str, bool]:
    """Interactive step-by-step CLI. Returns (models_str, scope, tools)."""
    print("\n" + "="*70)
    print("LLM-Eval Interactive Mode")
    print("="*70)

    # Step 1: Pick models
    print("\n[Step 1 of 3] Pick models")
    print("\nAvailable models:")
    for alias in sorted(MODELS):
        s = MODELS[alias]
        print(f"  {alias:<15} {s.protocol}/{s.model}")
    print("\nAvailable groups:")
    for g in sorted(GROUPS):
        print(f"  {g:<15} {', '.join(GROUPS[g])}")
    print("\n(You can also use 'protocol:model' syntax, e.g. 'freellm:gpt-4.1')")
    while True:
        models_str = input("\nEnter model(s) [comma-separated]: ").strip()
        if not models_str:
            print("  (required)")
            continue
        # Validate input: must be aliases, groups, or valid protocol:model syntax
        valid = True
        for tok in (t.strip() for t in models_str.split(",")):
            if tok not in MODELS and tok not in GROUPS:
                if ":" in tok:
                    proto = tok.split(":", 1)[0]
                    if proto not in _PROTOCOLS:
                        valid = False
                        break
                else:
                    # bare name without protocol:model syntax is invalid in interactive mode
                    valid = False
                    break
        if not valid:
            print(f"  ✗ '{tok}' not found. Use an alias, group, or protocol:model syntax. See --list")
            continue
        specs = resolve_models(models_str, default_protocol="ollama")
        if specs:
            print(f"  ✓ Resolved to: {', '.join(f'{s.protocol}/{s.model}' for s in specs)}")
            break
        print(f"  ✗ Could not resolve {models_str!r}. Check spelling or use --list")

    # Step 2: Pick scope
    print("\n[Step 2 of 3] Pick scope")
    for scope in sorted(SCOPES):
        task_count = len(SCOPES[scope]) if SCOPES[scope] else len(TASKS)
        print(f"  {scope:<10} {task_count} one-shot task(s)")
    while True:
        scope = input(f"\nEnter scope [{'/'.join(sorted(SCOPES))}]: ").strip() or "full"
        if scope in SCOPES:
            print(f"  ✓ Scope: {scope}")
            break
        print(f"  ✗ Unknown scope {scope!r}")

    # Step 3: Toggle tools
    print("\n[Step 3 of 3] Include the agentic tool-loop?")
    print("  (adds 2 extra tasks that let LLM fetch missing context)")
    while True:
        response = input("Include agentic tasks? [y/n]: ").strip().lower()
        if response in ("y", "yes"):
            tools = True
            print("  ✓ Tools: ON")
            break
        elif response in ("n", "no"):
            tools = False
            print("  ✓ Tools: OFF")
            break
        print("  (enter 'y' or 'n')")

    # Step 4: Review
    print("\n[Step 4 of 4] Review")
    specs = resolve_models(models_str, default_protocol="ollama")
    print(f"  Models:  {', '.join(f'{s.label}' for s in specs)}")
    print(f"  Scope:   {scope}")
    print(f"  Tools:   {'ON (agentic tasks enabled)' if tools else 'OFF (one-shot only)'}")
    while True:
        response = input("\nProceed? [y/n]: ").strip().lower()
        if response in ("y", "yes"):
            print("\n" + "="*70)
            return models_str, scope, tools
        elif response in ("n", "no"):
            print("Cancelled.")
            import sys
            sys.exit(0)
        print("  (enter 'y' or 'n')")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Simple LLM-eval runner: pick models, choose scope, toggle tools.",
        epilog="Run with --list to see the model menu.")
    ap.add_argument("--models",
                    help="aliases (qwen-coder), groups (local|frontier|all), raw protocol:model, "
                         "or bare names — comma-separated. See --list.")
    ap.add_argument("--scope", default="full", choices=list(SCOPES),
                    help="smaller/broader task set (default: full)")
    tools = ap.add_mutually_exclusive_group()
    tools.add_argument("--tools", dest="tools", action="store_true",
                       help="include the agentic tool-loop tasks (with-tools run)")
    tools.add_argument("--no-tools", dest="tools", action="store_false",
                       help="one-shot only (default)")
    ap.set_defaults(tools=False)
    ap.add_argument("--agentic", dest="tools", action="store_true", help=argparse.SUPPRESS)  # alias
    ap.add_argument("--protocol", default="ollama", choices=list(_PROTOCOLS),
                    help="default protocol for bare model names (default: ollama)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--list", action="store_true", help="print the model/scope menu and exit")
    ap.add_argument("--interactive", action="store_true", help="step-by-step interactive mode")
    args = ap.parse_args()

    if args.list:
        _print_menu()
        return 0

    # Interactive mode takes precedence
    if args.interactive:
        models_str, scope, tools = _interactive()
        args.models = models_str
        args.scope = scope
        args.tools = tools
    elif not args.models:
        ap.error("--models is required (or use --list to see options or --interactive for step-by-step)")

    specs = resolve_models(args.models, args.protocol)
    if not specs:
        ap.error(f"no models resolved from {args.models!r} — try --list")

    agentic_engine = None
    tmp = None
    if args.tools:
        import tempfile
        tmp = tempfile.mkdtemp(prefix="uci-llm-eval-")
        agentic_engine = _agentic_repo(Path(tmp))
    tasks = select_tasks(args.scope, args.tools, agentic_engine)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = str(EVALS / "reports" / "llm-logs" / f"llm-eval-{run_id}.jsonl")

    results = []
    try:
        for spec in specs:
            print(f"[llm-eval] {spec.protocol}/{spec.model}  "
                  f"scope={args.scope} tools={'on' if args.tools else 'off'}")
            results.append(evaluate_model(spec, args.timeout, tasks, log_path=log_path))
    finally:
        if agentic_engine is not None:
            agentic_engine.close()
        if tmp:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    report = {
        "run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tasks_version": TASKS_VERSION,
        "scope": args.scope,
        "tools": args.tools,
        "models": results,
    }
    out = EVALS / "reports" / f"llm-eval-{report['run'].replace(':', '').replace('-', '')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    ran_areas = [a for a in ("summaries", "capabilities", "candidates", "fields", "ask", "agentic")
                 if any(a in r["areas"] for r in results)]
    print(f"\n{'model':<24} {'proto':<8} {'overall':>7}  "
          + "  ".join(f"{a:>11}" for a in ran_areas))
    for r in sorted(results, key=lambda r: r["overall"], reverse=True):
        print(f"{r['model']:<24} {r.get('protocol', ''):<8} {r['overall']:>7.1f}  " + "  ".join(
            f"{r['areas'].get(a, 0):>11.2f}" if a in r["areas"] else f"{'—':>11}"
            for a in ran_areas))
    print(f"\nreport: {out.relative_to(EVALS.parent)}")
    if Path(log_path).exists():
        print(f"call log: {Path(log_path).relative_to(EVALS.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
