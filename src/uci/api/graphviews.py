"""Graph-explorer perspectives ("angles").

Lives in the API layer (not the engine) so it only composes stable public surfaces —
``engine.graph``, ``engine.graph_neighborhood``, ``engine.default_graph_root`` — into multi-seed
views for traversing relationships from different starting points:

* **repository** — the whole-tree root (default);
* **entry_points** — where execution starts (main guards, uncalled programs, JCL jobs, CICS trans,
  call-graph sources) and what they reach;
* **hubs** — the most depended-on symbols (highest call fan-in) and who depends on them;
* **modules** — the module/package structure.
"""

from __future__ import annotations

from collections import Counter

from ..core.entities import EntityType
from ..core.relationships import RelationType

GRAPH_VIEWS = (
    ("repository", "Repository (tree)"),
    ("entry_points", "Entry points"),
    ("hubs", "Most depended-on"),
    ("modules", "Modules"),
)


def _hub_seeds(engine, cap: int = 12) -> list[str]:
    indeg: Counter = Counter()
    for rel in engine.graph.relationships(RelationType.CALLS):
        indeg[rel.dst_id] += 1
    out: list[str] = []
    for eid, _n in indeg.most_common(40):
        entity = engine.graph.get_entity(eid)
        if entity and not entity.attributes.get("missing") and not entity.attributes.get("external"):
            out.append(eid)
        if len(out) >= cap:
            break
    return out


def _entry_point_seeds(engine, cap: int = 24) -> list[str]:
    called: set[str] = set()
    outdeg: Counter = Counter()
    for rel in engine.graph.relationships(RelationType.CALLS):
        called.add(rel.dst_id)
        outdeg[rel.src_id] += 1
    callable_kinds = {EntityType.FUNCTION, EntityType.METHOD, EntityType.TEST}
    seeds: list[str] = []
    for entity in engine.graph.entities(repo_id=engine.repo_id):
        if entity.attributes.get("missing") or entity.attributes.get("external"):
            continue
        if entity.kind == EntityType.JCL_JOB and not entity.attributes.get("proc"):
            seeds.append(entity.id)
        elif entity.kind == EntityType.TRANSACTION_CODE:
            seeds.append(entity.id)
        elif entity.kind == EntityType.LEGACY_PROGRAM and entity.id not in called:
            seeds.append(entity.id)
        elif entity.kind == EntityType.FUNCTION and entity.name in ("main", "__main__"):
            seeds.append(entity.id)
    # call-graph sources: callables nothing calls but that call others (top by out-degree)
    for eid, _n in sorted(((e, n) for e, n in outdeg.items() if e not in called),
                          key=lambda kv: kv[1], reverse=True):
        entity = engine.graph.get_entity(eid)
        if entity and entity.kind in callable_kinds and not entity.attributes.get("missing"):
            seeds.append(eid)
        if len(seeds) >= cap:
            break
    return list(dict.fromkeys(seeds))[:cap]


def _seeds(engine, view: str) -> list[str]:
    if view == "hubs":
        return _hub_seeds(engine)
    if view == "modules":
        return [e.id for e in list(engine.graph.entities(kind=EntityType.MODULE, repo_id=engine.repo_id))[:15]]
    if view == "entry_points":
        return _entry_point_seeds(engine)
    rid, _ = engine.default_graph_root()
    return [rid] if rid else []


def graph_view(engine, view: str, depth: int = 1, limit: int = 90) -> dict:
    """Union of the neighborhoods of a perspective's seed entities."""
    seeds = _seeds(engine, view)
    if not seeds:
        rid, _ = engine.default_graph_root()
        seeds = [rid] if rid else []
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen: set[tuple] = set()
    for seed_id in seeds:
        neighborhood = engine.graph_neighborhood(seed_id, depth=depth, limit=limit)
        if not neighborhood.get("ok"):
            continue
        for node in neighborhood["nodes"]:
            nodes[node["id"]] = node
        for edge in neighborhood["edges"]:
            key = (edge["source"], edge["target"], edge.get("type"))
            if key not in seen:
                seen.add(key)
                edges.append(edge)
        if len(nodes) >= limit:
            break
    edges = [e for e in edges if e["source"] in nodes and e["target"] in nodes]
    return {"ok": True, "view": view, "seeds": seeds, "nodes": list(nodes.values()),
            "edges": edges, "truncated": len(nodes) >= limit, "limit": limit}


__all__ = ["GRAPH_VIEWS", "graph_view"]
