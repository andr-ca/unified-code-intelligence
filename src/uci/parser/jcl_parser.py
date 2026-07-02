"""JCL structural parser — job → program execution edges (deterministic by construction).

Extracts (docs/lsp-refactoring-recommendations.md §3.6):
  - the JOB card                      -> JCL_JOB symbol
  - ``//STEP EXEC PGM=NAME``          -> RUNS link (job -> program)
  - ``//STEP EXEC PROC`` / ``EXEC NAME`` -> RUNS link with kind hint "job" (PROC member)
Symbolic targets (``PGM=&VAR``) are recorded as dynamic call sites, not edges.
"""

from __future__ import annotations

import re

from ..core.entities import EntityType
from .base import LanguageParser, ParsedCall, ParsedLink, ParsedSymbol, ParseResult

_RE_JOB = re.compile(r"^//([A-Z0-9$#@]+)\s+JOB\b", re.IGNORECASE)
_RE_PROC_CARD = re.compile(r"^//([A-Z0-9$#@]+)\s+PROC\b", re.IGNORECASE)
_RE_EXEC_PGM = re.compile(r"^//([A-Z0-9$#@]*)\s+EXEC\s+.*?\bPGM=([A-Z0-9$#@&.]+)", re.IGNORECASE)
_RE_EXEC_PROC = re.compile(r"^//([A-Z0-9$#@]*)\s+EXEC\s+(?:PROC=)?([A-Z0-9$#@]+)\s*(?:,|$)", re.IGNORECASE)
_RE_DD_DSN = re.compile(r"^//([A-Z0-9$#@]*)\s+DD\s+.*?\bDSN(?:AME)?=([A-Z0-9$#@.&()+-]+)", re.IGNORECASE)
_RE_DISP = re.compile(r"\bDISP=\(?([A-Z]+)", re.IGNORECASE)

#: DISP dispositions that imply the step writes the dataset (heuristic — labeled as such).
_WRITE_DISPS = frozenset({"NEW", "MOD"})


class JclParser(LanguageParser):
    language = "jcl"
    extensions = (".jcl", ".prc", ".proc")

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        is_proc = not path.lower().endswith(".jcl")
        job_name = module_qname
        job_line = 1
        n_lines = max(1, source.count("\n") + 1)
        steps: list[tuple[int, str, str, bool]] = []  # (line, step, target, symbolic)
        datasets: list[tuple[int, str, str, str]] = []  # (line, dd, dsn, relation)

        for i, raw in enumerate(source.splitlines(), start=1):
            if raw.startswith("//*") or not raw.startswith("//"):
                continue
            jm = _RE_JOB.match(raw)
            if jm:
                job_name = jm.group(1).upper()
                job_line = i
                continue
            pc = _RE_PROC_CARD.match(raw)
            if pc:
                is_proc = True
                job_name = pc.group(1).upper()
                job_line = i
                continue
            pm = _RE_EXEC_PGM.match(raw)
            if pm:
                target = pm.group(2).upper().rstrip(".")
                steps.append((i, pm.group(1).upper(), target, target.startswith("&")))
                continue
            dm = _RE_DD_DSN.match(raw)
            if dm:
                dsn = dm.group(2).upper()
                if dsn.startswith("&&") or "&" in dsn:
                    continue  # temporary / symbolic dataset — not a stable artifact
                disp = _RE_DISP.search(raw)
                relation = "writes" if disp and disp.group(1).upper() in _WRITE_DISPS else "reads"
                datasets.append((i, dm.group(1).upper(), dsn, relation))
                continue
            cm = _RE_EXEC_PROC.match(raw)
            if cm and "PGM=" not in raw.upper():
                proc = cm.group(2).upper()
                if proc not in ("JOB", "PROC", "PEND"):
                    steps.append((i, cm.group(1).upper(), proc, proc.startswith("&")))

        # qualified name = member stem (how other JCL/schedulers reference this job/proc);
        # display name = the JOB/PROC card name (may differ)
        result.symbols.append(ParsedSymbol(
            name=job_name, qualified_name=module_qname, kind=EntityType.JCL_JOB,
            start_line=job_line, end_line=n_lines,
            attributes={"job_card": job_name, "file_module": module_qname, "proc": is_proc},
        ))
        for line, dd, dsn, relation in datasets:
            result.links.append(ParsedLink(
                relation=relation, src_qname=module_qname, target_name=dsn,
                target_kind=EntityType.DATASET.value, start_line=line,
                attributes={"dd": dd, "heuristic": "disp"},
            ))
        for line, step, target, symbolic in steps:
            if symbolic:
                # &VAR — resolvable only with SET/PROC context; recorded as a dynamic site
                result.calls.append(ParsedCall(
                    callee_name=target.lstrip("&"), caller_qname=module_qname,
                    start_line=line, dynamic=True,
                ))
            else:
                result.links.append(ParsedLink(
                    relation="runs", src_qname=module_qname, target_name=target,
                    target_kind=EntityType.LEGACY_PROGRAM.value, start_line=line,
                    attributes={"step": step} if step else {},
                ))
        return result


__all__ = ["JclParser"]
