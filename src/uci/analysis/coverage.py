"""Coverage & blind spots — the honest "what we could NOT do" signals for the Understand view.

Purely structural (no LLM): files scanned but not parsed into a known language or yielding no
symbols, and callable symbols nothing in the repo references (likely-unused — a heuristic). Composed
by :meth:`uci.engine.Engine.understand`. These are surfaced with caveats and never drive traversal.
"""

from __future__ import annotations

from ..core.entities import EntityType
from ..core.interfaces import GraphStore
from ..core.relationships import RelationType

#: Inbound edge types that mean "something in the repo uses this symbol".
_USED_BY = frozenset({RelationType.CALLS, RelationType.REFERENCES})

#: Names never flagged as unused — conventional entry/framework/test surfaces.
_ENTRY_NAMES = frozenset({"main", "__main__", "run", "start", "handler", "app", "setup", "teardown"})

#: Languages that mean "indexed but not parsed into structure".
_SHALLOW_LANGS = frozenset({"unknown", "text", ""})


def _shallow_entry(entity, graph: GraphStore) -> dict | None:
    """A file/module we scanned but did not parse into structure, or ``None``."""
    lang = (entity.language or "").lower()
    if entity.kind == EntityType.FILE and lang in _SHALLOW_LANGS:
        return {"path": entity.provenance.path, "language": entity.language or "unknown",
                "reason": "unrecognized language — indexed as text only"}
    if (entity.kind == EntityType.MODULE and lang != "config"
            and not graph.out_relationships(entity.id, [RelationType.DEFINES])):
        return {"path": entity.provenance.path, "language": entity.language or "",
                "reason": "no symbols extracted"}
    return None


def _unused_entry(entity, used: set[str]) -> dict | None:
    """A callable nothing in the repo references and that is not a conventional entry, or ``None``."""
    if entity.kind not in (EntityType.FUNCTION, EntityType.METHOD) or entity.id in used:
        return None
    name = entity.name.lower()
    path = entity.provenance.path.lower()
    if name in _ENTRY_NAMES or name.startswith("__") or name.startswith("test_") or "test" in path:
        return None
    return {"name": entity.name, "qualified_name": entity.qualified_name,
            "kind": entity.kind.value, "path": entity.provenance.path,
            "entity_id": entity.id, "start_line": entity.provenance.start_line}


def coverage_report(graph: GraphStore, repo_id: str, limit: int = 30) -> dict:
    """Files parsed shallowly + symbols nothing references. Both are heuristics."""
    used = {rel.dst_id for rel in graph.relationships() if rel.type in _USED_BY}
    shallow: list[dict] = []
    unused: list[dict] = []
    for entity in graph.entities(repo_id=repo_id):
        if entity.attributes.get("missing") or entity.attributes.get("external"):
            continue
        shallow_hit = _shallow_entry(entity, graph)
        if shallow_hit:
            shallow.append(shallow_hit)
        unused_hit = _unused_entry(entity, used)
        if unused_hit:
            unused.append(unused_hit)

    shallow.sort(key=lambda s: s["path"])
    unused.sort(key=lambda u: u["qualified_name"])
    return {
        "shallow_files": shallow[:limit], "shallow_files_total": len(shallow),
        "possibly_unused": unused[:limit], "possibly_unused_total": len(unused),
    }


__all__ = ["coverage_report"]
