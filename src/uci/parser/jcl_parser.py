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
_RE_EXEC_PGM = re.compile(r"^//([A-Z0-9$#@]*)\s+EXEC\s+.*?\bPGM=([A-Z0-9$#@&.]+)", re.IGNORECASE)
_RE_EXEC_PROC = re.compile(r"^//([A-Z0-9$#@]*)\s+EXEC\s+(?:PROC=)?([A-Z0-9$#@]+)\s*(?:,|$)", re.IGNORECASE)


class JclParser(LanguageParser):
    language = "jcl"
    extensions = (".jcl",)

    def parse(self, source: str, path: str, module_qname: str) -> ParseResult:
        result = ParseResult(language=self.language, module_qname=module_qname)
        job_name = module_qname
        job_line = 1
        n_lines = max(1, source.count("\n") + 1)
        steps: list[tuple[int, str, str, bool]] = []  # (line, step, target, symbolic)

        for i, raw in enumerate(source.splitlines(), start=1):
            if raw.startswith("//*") or not raw.startswith("//"):
                continue
            jm = _RE_JOB.match(raw)
            if jm:
                job_name = jm.group(1).upper()
                job_line = i
                continue
            pm = _RE_EXEC_PGM.match(raw)
            if pm:
                target = pm.group(2).upper().rstrip(".")
                steps.append((i, pm.group(1).upper(), target, target.startswith("&")))
                continue
            cm = _RE_EXEC_PROC.match(raw)
            if cm and "PGM=" not in raw.upper():
                proc = cm.group(2).upper()
                if proc not in ("JOB",):
                    steps.append((i, cm.group(1).upper(), proc, proc.startswith("&")))

        # qualified name = member stem (how other JCL/schedulers reference this job);
        # display name = the JOB card name (may differ)
        result.symbols.append(ParsedSymbol(
            name=job_name, qualified_name=module_qname, kind=EntityType.JCL_JOB,
            start_line=job_line, end_line=n_lines,
            attributes={"job_card": job_name, "file_module": module_qname},
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
