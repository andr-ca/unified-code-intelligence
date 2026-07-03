"""Content-first language analysis.

Classify a file by its *content* (deterministic signatures), using the file extension only as a
tiebreaker. This rescues extensionless or mislabeled files — most importantly mainframe PDS
members, which are routinely exported without a suffix (a COBOL program simply named ``CBTRN02C``,
JCL in a ``.txt``, HLASM with no extension). Dependency-free and deterministic, so it never
introduces nondeterminism into the index.

Config formats (JSON/YAML/INI/TOML/.env) are intentionally *not* content-detected — they are
unreliable to sniff and always carry an extension, so they fall through to the extension path.
"""

from __future__ import annotations

import re

from .langdetect import detect_language

# Per language, a list of (signature, weight). Signatures are specific to the language and appear
# near the top of real files; scores are summed and the highest wins. A classification also
# requires >= _MIN_SIGNATURES distinct matches so a single incidental marker (e.g. a COBOL snippet
# quoted in prose) does not reclassify a document as code.
_SIGNATURES: dict[str, list[tuple[re.Pattern[str], int]]] = {
    "cobol": [
        (re.compile(r"^[\s\d]*IDENTIFICATION\s+DIVISION", re.I | re.M), 5),
        (re.compile(r"^[\s\d]*PROGRAM-ID\b", re.I | re.M), 4),
        (re.compile(r"^[\s\d]*(ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION", re.I | re.M), 4),
        (re.compile(r"^[\s\d]*WORKING-STORAGE\s+SECTION", re.I | re.M), 3),
        (re.compile(r"\bPIC(TURE)?\s+[X9SVAZ()]+", re.I), 2),
        (re.compile(r"^[\s\d]*\d{2}\s+[A-Z0-9-]+.*\b(PIC|OCCURS|VALUE|REDEFINES)\b", re.I | re.M), 2),
        (re.compile(r"^[\s\d]*(PERFORM|COMPUTE|EVALUATE|MOVE)\b", re.I | re.M), 1),
        (re.compile(r"\bEXEC\s+(CICS|SQL)\b", re.I), 2),
        (re.compile(r"^[\s\d]*COPY\s+[A-Z0-9-]+", re.I | re.M), 2),
    ],
    "jcl": [
        (re.compile(r"^//\S*\s+JOB\b", re.M), 5),
        (re.compile(r"^//\S*\s+EXEC\s+(PGM=|PROC=|\S)", re.M), 4),
        (re.compile(r"^//\S+\s+DD\b", re.M), 3),
        (re.compile(r"^//\S+\s+(PROC|PEND)\b", re.M), 3),
        (re.compile(r"^//\*", re.M), 1),
        (re.compile(r"^/\*", re.M), 1),
    ],
    "hlasm": [
        (re.compile(r"^\S+\s+(CSECT|DSECT|RSECT|START)\b", re.M), 5),
        (re.compile(r"^\S*\s+(DC|DS)\s+[FHCXPZAVBTDE]", re.M), 3),
        (re.compile(r"^\s+(USING|DROP|BALR|BASR|STM|LM|LTORG)\b", re.M), 2),
        (re.compile(r"^\S+\s+EQU\b", re.M), 2),
        (re.compile(r"^\s+(END|SVC|BR)\b", re.M), 1),
    ],
    "bms": [
        (re.compile(r"\bDFHMSD\b"), 5),
        (re.compile(r"\bDFHMDI\b"), 4),
        (re.compile(r"\bDFHMDF\b"), 4),
    ],
    "csd": [
        (re.compile(r"^\s*DEFINE\s+", re.I | re.M), 3),
        (re.compile(r"\bGROUP\(", re.I), 3),
        (re.compile(r"\b(PROGRAM|TRANSACTION|MAPSET|TDQUEUE|FILE)\(", re.I), 2),
    ],
    "python": [
        (re.compile(r"^#!.*\bpython", re.M), 4),
        (re.compile(r"^\s*(def|class)\s+\w+", re.M), 3),
        (re.compile(r"^\s*(import\s+\w|from\s+[\w.]+\s+import)\b", re.M), 3),
        (re.compile(r"""if\s+__name__\s*==\s*['"]__main__['"]"""), 3),
        (re.compile(r"^\s*@\w[\w.]*\s*$", re.M), 1),
    ],
    "javascript": [
        (re.compile(r"^\s*import\s+.*\bfrom\b", re.M), 3),
        (re.compile(r"^\s*export\s+(default\s+|const\s+|function\b|class\b)", re.M), 3),
        (re.compile(r"\b(const|let)\s+\w+\s*=", re.M), 2),
        (re.compile(r"\b(function\s+\w+|require\s*\()", re.M), 2),
        (re.compile(r"\b(interface|type)\s+\w+\s*[={]", re.M), 2),
        (re.compile(r"=>"), 1),
    ],
}

#: minimum summed weight for content to be treated as confident.
_MIN_CONFIDENCE = 4
#: minimum number of distinct signatures that must match (guards against a lone incidental marker).
_MIN_SIGNATURES = 2


def _score(content: str, signatures: list[tuple[re.Pattern[str], int]]) -> tuple[int, int]:
    total = 0
    matched = 0
    for pattern, weight in signatures:
        if pattern.search(content):
            total += weight
            matched += 1
    return total, matched


def analyze_language(path: str, content: str | None) -> str | None:
    """Classify ``content`` content-first, returning a language id or ``None`` for non-code.

    Content signatures decide when confident; the extension (:func:`detect_language`) breaks
    near-ties and is the fallback for inconclusive content (prose, config, unknown files).
    """
    ext_lang = detect_language(path)
    if not content:
        return ext_lang

    scored = {lang: _score(content, sigs) for lang, sigs in _SIGNATURES.items()}
    best_lang, (best_total, best_matched) = max(scored.items(), key=lambda kv: kv[1][0])

    if best_total >= _MIN_CONFIDENCE and best_matched >= _MIN_SIGNATURES:
        # Content is confident. Let the extension win only a genuine near-tie (its own content
        # score is within one point of the best), so a correctly-named file is never flipped by
        # a marginally higher-scoring look-alike.
        ext_total = scored.get(ext_lang, (0, 0))[0] if ext_lang else 0
        if ext_lang in scored and ext_total > 0 and ext_total >= best_total - 1:
            return ext_lang
        return best_lang

    # Inconclusive content → trust the extension (covers config/text/unknown as before).
    return ext_lang


__all__ = ["analyze_language"]
