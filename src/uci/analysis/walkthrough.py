"""'Follow a thread' worked example for the Understand tutorial.

Picks one real entry point and traces its actual downstream path — the concrete flow a newcomer
watches end-to-end. Purely structural graph traversal; composed by ``Engine.understand``.
"""

from __future__ import annotations

from ..core.entities import EntityType
from ..core.interfaces import GraphStore
from ..core.relationships import RelationType

_TRIGGER_KINDS = frozenset({EntityType.TRANSACTION_CODE, EntityType.JCL_JOB})
_TRIGGER_EDGES = [RelationType.INVOKES, RelationType.RUNS]
_DATA_EDGES = [RelationType.READS, RelationType.WRITES]
_MAIN_NAMES = frozenset({"main", "__main__"})


def _hit(entity) -> dict:
    return {"entity_id": entity.id, "kind": entity.kind.value, "name": entity.name,
            "qualified_name": entity.qualified_name, "path": entity.provenance.path,
            "summary": entity.attributes.get("summary", "")}


def _target_of(graph: GraphStore, entity):
    """The program an entry drives: the invoked/run program for triggers, else the entry itself."""
    if entity.kind not in _TRIGGER_KINDS:
        return entity
    for rel in graph.out_relationships(entity.id, _TRIGGER_EDGES):
        prog = graph.get_entity(rel.dst_id)
        if prog and not prog.attributes.get("missing"):
            return prog
    return None


def _downstream(graph: GraphStore, program) -> tuple[list[dict], list[dict]]:
    calls = [_hit(d) for d in (graph.get_entity(r.dst_id)
             for r in graph.out_relationships(program.id, [RelationType.CALLS]))
             if d and not d.attributes.get("missing")]
    data = []
    for rel in graph.out_relationships(program.id, _DATA_EDGES):
        tbl = graph.get_entity(rel.dst_id)
        if tbl:
            data.append({**_hit(tbl),
                         "access": "read" if rel.type == RelationType.READS else "write"})
    return calls, data


def _candidates(graph: GraphStore, repo_id: str):
    called = {rel.dst_id for rel in graph.relationships(RelationType.CALLS)}
    for entity in graph.entities(repo_id=repo_id):
        if entity.attributes.get("missing") or entity.attributes.get("external"):
            continue
        if entity.kind in _TRIGGER_KINDS:
            yield entity
        elif entity.kind == EntityType.LEGACY_PROGRAM and entity.id not in called:
            yield entity
        elif entity.kind == EntityType.FUNCTION and entity.name in _MAIN_NAMES:
            yield entity


def _verb(entity) -> str:
    if entity.kind == EntityType.JCL_JOB:
        return "runs"
    if entity.kind == EntityType.TRANSACTION_CODE:
        return "invokes"
    return "starts at"


def walkthrough(graph: GraphStore, repo_id: str) -> dict:
    """One concrete entry point traced through its call + data chain — the richest one found."""
    best: tuple | None = None
    best_score = 0
    for entry in _candidates(graph, repo_id):
        target = _target_of(graph, entry)
        if target is None:
            continue
        calls, data = _downstream(graph, target)
        score = len(calls) + len(data)
        if score > best_score:
            best, best_score = (entry, target, calls, data), score
    if best is None:
        return {}
    entry, target, calls, data = best
    capability = None
    for rel in graph.out_relationships(target.id, [RelationType.IMPLEMENTS_CAPABILITY]):
        cap = graph.get_entity(rel.dst_id)
        if cap:
            capability = {"name": cap.name, "entity_id": cap.id}
            break
    return {
        "entry": _hit(entry), "target": _hit(target), "same": entry.id == target.id,
        "verb": _verb(entry), "calls": calls[:6], "data": data[:8], "capability": capability,
    }


__all__ = ["walkthrough"]
