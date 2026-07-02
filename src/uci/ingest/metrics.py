"""Codebase metrics collected during indexing.

Two layers, both cheap because the indexer already reads every file and holds the whole graph
in memory at build time:

  - **line stats** (per language): code / comment / blank line counts, classified with each
    language family's comment syntax (COBOL column-7, JCL ``//*``, HLASM column-1 ``*``, …);
  - **graph stats**: entry points, cross-file dependency counts, call-edge resolution
    distribution (the determinism scoreboard), fan-in hubs, external/missing tallies.

Persisted as index state under ``code_metrics`` and served by ``Engine.metrics()`` /
``uci metrics`` / the ``get_code_metrics`` MCP tool.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..core.entities import Entity, EntityType
from ..core.relationships import Relationship, RelationType

#: Line-comment / block-comment rules per language family.
_HASH = ("#", ";")
_MAIN_GUARD = "__main__"

#: Dependency-like relations counted as cross-file dependencies.
_DEPENDENCY_TYPES = frozenset({
    RelationType.CALLS, RelationType.IMPORTS, RelationType.DEPENDS_ON, RelationType.REFERENCES,
    RelationType.RUNS, RelationType.INVOKES, RelationType.EXTENDS, RelationType.IMPLEMENTS,
    RelationType.READS, RelationType.WRITES, RelationType.MAPS_TO, RelationType.USES,
})

#: Relations whose absence of inbound edges marks a mainframe program as an entry point.
_INBOUND_ENTRY_TYPES = frozenset({RelationType.CALLS, RelationType.RUNS, RelationType.INVOKES})


def line_stats(source: str, language: str) -> dict[str, int]:
    """Classify each line as code / comment / blank for *language*."""
    code = comment = blank = 0
    in_block = False  # JS /* */ state
    for raw in source.splitlines():
        stripped = raw.strip()
        if not stripped:
            blank += 1
            continue
        if language == "javascript":
            if in_block:
                comment += 1
                if "*/" in stripped:
                    in_block = False
                continue
            if stripped.startswith("//"):
                comment += 1
            elif stripped.startswith("/*"):
                comment += 1
                if "*/" not in stripped:
                    in_block = True
            else:
                code += 1
        elif language == "cobol":
            if len(raw) >= 7 and raw[6] in ("*", "/"):
                comment += 1
            elif stripped.startswith("*"):
                comment += 1
            else:
                code += 1
        elif language == "jcl":
            comment += 1 if raw.startswith("//*") else 0
            code += 0 if raw.startswith("//*") else 1
        elif language in ("hlasm", "bms", "csd"):
            if raw.startswith("*") or stripped.startswith(".*"):
                comment += 1
            else:
                code += 1
        else:  # python, config, and default hash-comment families
            if stripped.startswith(_HASH):
                comment += 1
            else:
                code += 1
    return {"code": code, "comment": comment, "blank": blank,
            "total": code + comment + blank}


class MetricsCollector:
    """Accumulates per-file line stats during the scan pass, then derives graph metrics."""

    def __init__(self) -> None:
        self.by_language: dict[str, Counter] = defaultdict(Counter)
        self.python_main_guards = 0

    # -- scan pass ------------------------------------------------------
    def add_file(self, path: str, language: str, source: str) -> None:
        stats = line_stats(source, language)
        bucket = self.by_language[language]
        bucket["files"] += 1
        for key, value in stats.items():
            bucket[key] += value
        if language == "python" and _MAIN_GUARD in source:
            self.python_main_guards += 1

    # -- graph pass -----------------------------------------------------
    def finalize(self, entities: list[Entity], relationships: list[Relationship],
                 unresolved_calls: list[dict], gap_count: int) -> dict:
        by_kind = Counter(e.kind.value for e in entities)
        by_type = Counter(r.type.value for r in relationships)

        paths = {e.id: e.provenance.path for e in entities}
        cross_file = cross_dir = 0
        in_degree: Counter = Counter()
        resolution_dist: Counter = Counter()
        for rel in relationships:
            if rel.type == RelationType.CALLS:
                in_degree[rel.dst_id] += 1
                resolution_dist[rel.attributes.get("resolution", "unlabeled")] += 1
            if rel.type not in _DEPENDENCY_TYPES:
                continue
            sp, dp = paths.get(rel.src_id, ""), paths.get(rel.dst_id, "")
            if sp and dp and sp != dp:
                cross_file += 1
                if sp.rsplit("/", 1)[0] != dp.rsplit("/", 1)[0]:
                    cross_dir += 1

        entry_points = self._entry_points(entities, relationships)
        entry_points["python_main_guards"] = self.python_main_guards

        hubs = sorted(
            ((eid, n) for eid, n in in_degree.items()),
            key=lambda kv: kv[1], reverse=True,
        )[:5]
        by_id = {e.id: e for e in entities}
        top_fan_in = [
            {"name": by_id[eid].qualified_name, "kind": by_id[eid].kind.value, "callers": n}
            for eid, n in hubs if eid in by_id and not by_id[eid].attributes.get("missing")
        ]

        totals = Counter()
        for bucket in self.by_language.values():
            totals.update(bucket)

        return {
            "files": int(totals["files"]),
            "lines": {
                "total": int(totals["total"]), "code": int(totals["code"]),
                "comment": int(totals["comment"]), "blank": int(totals["blank"]),
                "comment_ratio": round(totals["comment"] / totals["total"], 3) if totals["total"] else 0.0,
            },
            "by_language": {
                lang: {k: int(v) for k, v in sorted(bucket.items())}
                for lang, bucket in sorted(self.by_language.items())
            },
            "entry_points": entry_points,
            "cross_dependencies": {"cross_file_edges": cross_file, "cross_directory_edges": cross_dir},
            "entities_by_kind": dict(by_kind.most_common()),
            "relationships_by_type": dict(by_type.most_common()),
            "call_resolution_distribution": dict(resolution_dist.most_common()),
            "dynamic_call_sites": sum(1 for u in unresolved_calls
                                      if u.get("reason") in ("dynamic-target", "dynamic-receiver")),
            "unresolved_call_sites": len(unresolved_calls),
            "external_dependencies": sum(1 for e in entities if e.attributes.get("external")),
            "missing_artifacts": gap_count,
            "top_fan_in": top_fan_in,
        }

    def _entry_points(self, entities: list[Entity], relationships: list[Relationship]) -> dict:
        inbound: set[str] = {
            rel.dst_id for rel in relationships if rel.type in _INBOUND_ENTRY_TYPES
        }
        jobs = transactions = uncalled_programs = 0
        for e in entities:
            if e.attributes.get("missing") or e.attributes.get("external"):
                continue
            if e.kind == EntityType.JCL_JOB and not e.attributes.get("proc"):
                jobs += 1
            elif e.kind == EntityType.TRANSACTION_CODE:
                transactions += 1
            elif e.kind == EntityType.LEGACY_PROGRAM and e.id not in inbound:
                uncalled_programs += 1
        total = jobs + transactions + uncalled_programs + self.python_main_guards
        return {
            "total": total,
            "jcl_jobs": jobs,
            "cics_transactions": transactions,
            "uncalled_programs": uncalled_programs,
        }


__all__ = ["MetricsCollector", "line_stats"]
