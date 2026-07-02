#!/usr/bin/env python3
"""Independent ground-truth miner for the UCI eval datasets.

Extracts *literal, deterministic-by-construction* facts from mainframe sources:
  - COBOL:  CALL 'PROG'            -> call edges (per program)
            COPY MEMBER            -> copybook dependents (fan-in)
            EXEC SQL ... FROM/INTO -> table reads/writes (per program)
  - JCL:    EXEC PGM=NAME          -> job -> program
  - CSD:    DEFINE TRANSACTION(T) ... PROGRAM(P) -> transaction -> program

INDEPENDENCE RULE (evals/docs/evaluation.md §4): this module must NEVER import
from `uci.*`. It is the reference implementation the tool is scored against.

Usage:
    python3 evals/tools/mine_ground_truth.py <repo-dir> [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# System/vendor names that are external by convention (never "missing" artifacts).
EXTERNAL_PREFIXES = ("DFH", "DSN", "CEE", "DFS", "CBL", "IGZ", "ILBO", "MQ", "CSQ", "SQL")
EXTERNAL_UTILITIES = {
    "IDCAMS", "IEBGENER", "IEFBR14", "SORT", "ICETOOL", "IKJEFT01", "IKJEFT1A",
    "IKJEFT1B", "SDSF", "FTP", "DFHCSDUP", "DSNTIAD", "DSNTEP2", "IEBCOPY", "ADRDSSU",
}

RE_CALL = re.compile(r"\bCALL\s+'([A-Z0-9$#@-]+)'", re.IGNORECASE)
RE_COPY = re.compile(r"\bCOPY\s+([A-Z0-9$#@-]+)\s*\.?", re.IGNORECASE)
RE_SQL_INCLUDE = re.compile(r"\bINCLUDE\s+([A-Z0-9$#@-]+)", re.IGNORECASE)
RE_EXEC_PGM = re.compile(r"\bEXEC\s+PGM=([A-Z0-9$#@&.]+)", re.IGNORECASE)
RE_EXEC_PROC = re.compile(r"\bEXEC\s+(?:PROC=)?([A-Z0-9$#@]+)\s*$")
RE_TRAN = re.compile(r"DEFINE\s+TRANSACTION\s*\(\s*([A-Z0-9$#@]+)\s*\)", re.IGNORECASE)
RE_TRAN_PGM = re.compile(r"\bPROGRAM\s*\(\s*([A-Z0-9$#@-]+)\s*\)", re.IGNORECASE)
RE_SQL_READ = re.compile(r"\bFROM\s+([A-Z_][A-Z_0-9]*(?:\.[A-Z_][A-Z_0-9]*)?)", re.IGNORECASE)
RE_SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Z_][A-Z_0-9]*(?:\.[A-Z_][A-Z_0-9]*)?)",
    re.IGNORECASE,
)
# Dynamic dispatch markers (recorded as evidence for completeness expectations, not as edges)
RE_DYNAMIC = re.compile(
    r"\bCALL\s+([A-Z][A-Z0-9-]*)\b(?!\s*')|XCTL\s+PROGRAM\s*\(\s*([A-Z][A-Z0-9-]{0,7}-[A-Z0-9-]+)\s*\)"
    r"|LINK\s+PROGRAM\s*\(\s*([A-Z][A-Z0-9-]{0,7}-[A-Z0-9-]+)\s*\)",
    re.IGNORECASE,
)

SQL_NOISE = {"SYSIBM", "SQLCA", "SQLDA", "DUAL"}  # not application tables


def cobol_lines(path: Path):
    """Yield (lineno, code_text) for fixed-format COBOL, skipping comment lines
    (* or / in column 7) and sequence columns (1-6, 73-80)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for i, raw in enumerate(text.splitlines(), start=1):
        if len(raw) >= 7 and raw[6] in ("*", "/"):
            continue
        yield i, raw[7:72] if len(raw) > 7 else raw


def jcl_lines(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for i, raw in enumerate(text.splitlines(), start=1):
        if raw.startswith("//*"):
            continue
        yield i, raw


def is_external(name: str) -> bool:
    up = name.upper()
    return up in EXTERNAL_UTILITIES or any(up.startswith(p) for p in EXTERNAL_PREFIXES)


def mine(repo: Path) -> dict:
    cbl_files = sorted(
        p for p in repo.rglob("*")
        if p.suffix.lower() in (".cbl", ".cob") and ".git" not in p.parts
    )
    cpy_files = {p.stem.upper(): p for p in repo.rglob("*") if p.suffix.lower() == ".cpy" and ".git" not in p.parts}
    jcl_files = sorted(p for p in repo.rglob("*.jcl") if ".git" not in p.parts)
    csd_files = sorted(p for p in repo.rglob("*") if p.suffix.lower() == ".csd" and ".git" not in p.parts)
    program_names = {p.stem.upper() for p in cbl_files}

    calls: dict[str, dict] = {}
    copy_dependents: dict[str, set] = defaultdict(set)
    data_access: dict[str, dict] = {}
    dynamic_sites: dict[str, list] = defaultdict(list)

    for cbl in cbl_files:
        prog = cbl.stem.upper()
        internal, external, missing = set(), set(), set()
        reads, writes = set(), set()
        in_sql = False
        for lineno, line in cobol_lines(cbl):
            for m in RE_CALL.finditer(line):
                name = m.group(1).upper()
                if name in program_names:
                    internal.add(name)
                elif is_external(name):
                    external.add(name)
                else:
                    missing.add(name)
            for m in RE_COPY.finditer(line):
                member = m.group(1).upper()
                if member == "OF":  # `COPY xx OF yy` prose false-positive guard
                    continue
                copy_dependents[member].add(prog)
            up = line.upper()
            if "EXEC SQL" in up:
                in_sql = True
            if in_sql:
                for m in RE_SQL_INCLUDE.finditer(line):
                    copy_dependents[m.group(1).upper()].add(prog)
                for m in RE_SQL_READ.finditer(line):
                    t = m.group(1).upper()
                    if t.split(".")[0] not in SQL_NOISE:
                        reads.add(t)
                for m in RE_SQL_WRITE.finditer(line):
                    t = m.group(1).upper()
                    if t.split(".")[0] not in SQL_NOISE:
                        writes.add(t)
                if "END-EXEC" in up:
                    in_sql = False
            for m in RE_DYNAMIC.finditer(line):
                var = next(g for g in m.groups() if g)
                if var.upper() not in program_names and "-" in var:  # data-name, not a literal
                    dynamic_sites[prog].append({"line": lineno, "via": var.upper()})
        if internal or external or missing:
            calls[prog] = {"internal": sorted(internal), "external": sorted(external),
                           "unclassified": sorted(missing)}
        if reads or writes:
            data_access[prog] = {"reads": sorted(reads), "writes": sorted(writes)}

    jobs: dict[str, dict] = {}
    for jcl in jcl_files:
        job = jcl.stem.upper()
        internal, external, procs = set(), set(), set()
        for _lineno, line in jcl_lines(jcl):
            matched_pgm = False
            for m in RE_EXEC_PGM.finditer(line):
                matched_pgm = True
                name = m.group(1).upper().rstrip(".")
                if name.startswith("&"):
                    continue  # symbolic — unresolved by construction
                (internal if name in program_names else external).add(name)
            if not matched_pgm and line.startswith("//") and "PGM=" not in line.upper():
                pm = re.match(r"^//([A-Z0-9$#@]*)\s+EXEC\s+(?:PROC=)?([A-Z0-9$#@]+)", line)
                if pm and pm.group(2).upper() not in ("PGM",):
                    procs.add(pm.group(2).upper())
        if internal or external or procs:
            jobs[job] = {"path": str(jcl.relative_to(repo)),
                         "programs_internal": sorted(internal),
                         "programs_external": sorted(external),
                         "procs": sorted(procs)}

    transactions = []
    for csd in csd_files:
        text = csd.read_text(encoding="utf-8", errors="replace")
        for block in re.split(r"(?=DEFINE\s)", text):
            tm = RE_TRAN.search(block)
            if not tm:
                continue
            pm = RE_TRAN_PGM.search(block)
            if pm:
                transactions.append({"tran": tm.group(1).upper(), "program": pm.group(1).upper()})

    copybooks = {
        member: {
            "exists_in_repo": member in cpy_files,
            "path": str(cpy_files[member].relative_to(repo)) if member in cpy_files else None,
            "external": is_external(member),
            "dependents": sorted(deps),
        }
        for member, deps in sorted(copy_dependents.items())
    }

    return {
        "repo": str(repo),
        "programs": {p.stem.upper(): str(p.relative_to(repo)) for p in cbl_files},
        "calls": calls,
        "copybooks": copybooks,
        "jobs": jobs,
        "transactions": sorted(transactions, key=lambda t: t["tran"]),
        "data_access": data_access,
        "dynamic_dispatch": {k: v for k, v in sorted(dynamic_sites.items())},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("repo", type=Path)
    ap.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    args = ap.parse_args()
    facts = mine(args.repo.resolve())
    payload = json.dumps(facts, indent=2)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
        counts = {k: len(v) for k, v in facts.items() if isinstance(v, dict)}
        print(f"wrote {args.out} ({counts})")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
