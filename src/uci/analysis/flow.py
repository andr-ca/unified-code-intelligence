"""Flow-level block schemes — the *flow between* programs (Tier-1), complementing the CFG's *logic
inside* a routine (Tier-2, ``analysis/cfg.py``).

A flow diagram traces a business flow across the graph from an anchor (a transaction, JCL job,
business capability, or program): control edges (``INVOKES``/``RUNS``/``CALLS``) expand the reachable
programs, and each program's data (``READS``/``WRITES``) and screens (``USES``) attach as leaves. It
is a deterministic, on-demand analysis artifact — a Mermaid rendering over edges the graph already
has, no new parsing — and it composes with the CFG: a flow node is a program you can then open with
``control_flow`` to see the logic inside it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..core.entities import EntityType
from ..core.interfaces import GraphStore
from ..core.relationships import RelationType

_CONTROL_EDGES = [RelationType.INVOKES, RelationType.RUNS, RelationType.CALLS]
_DATA_EDGES = [RelationType.READS, RelationType.WRITES]
_SCREEN_EDGES = [RelationType.USES]

_TRIGGER_KINDS = frozenset({EntityType.TRANSACTION_CODE, EntityType.JCL_JOB})
_DATA_KINDS = frozenset({EntityType.DATABASE_TABLE, EntityType.DATASET})


@dataclass
class FlowNode:
    id: str
    kind: str
    label: str
    path: str = ""


@dataclass
class FlowEdge:
    src: str
    dst: str
    label: str = ""


@dataclass
class FlowGraph:
    anchor: str
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    truncated: bool = False

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for n in self.nodes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        programs = sum(v for k, v in kinds.items()
                       if k in ("legacy_program", "module", "function", "method"))
        data = sum(v for k, v in kinds.items() if k in ("database_table", "dataset"))
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "programs": programs, "data": data, "screens": kinds.get("screen", 0),
                "kinds": kinds, "truncated": self.truncated}

    def to_dict(self) -> dict:
        return {"anchor": self.anchor, "nodes": [asdict(n) for n in self.nodes],
                "edges": [asdict(e) for e in self.edges], "stats": self.stats(),
                "mermaid": self.to_mermaid()}

    def to_mermaid(self) -> str:
        lines = ["flowchart LR"]
        for n in self.nodes:
            lines.append(f"  {n.id}{_shape(n.kind, n.label)}")
        for e in self.edges:
            arrow = f"-->|{_esc(e.label)}|" if e.label else "-->"
            lines.append(f"  {e.src} {arrow} {e.dst}")
        return "\n".join(lines)


def _esc(text: str) -> str:
    t = " ".join(str(text).split())[:50].replace('"', "'").replace("[", "(").replace("]", ")")
    return t or "…"


def _shape(kind: str, label: str) -> str:
    t = _esc(label)
    if kind in ("transaction_code", "jcl_job"):
        return f'(["{t}"])'          # trigger — stadium
    if kind == "business_capability":
        return f'{{{{"{t}"}}}}'      # capability — hexagon
    if kind in ("database_table", "dataset"):
        return f'[("{t}")]'          # data — cylinder
    if kind == "screen":
        return f'>"{t}"]'            # screen — flag
    return f'["{t}"]'                # program — rectangle


def _node_id(entity_id: str, used: dict[str, str]) -> str:
    if entity_id not in used:
        used[entity_id] = f"f{len(used)}"
    return used[entity_id]


def build_flow(graph: GraphStore, roots: list, depth: int = 3, max_nodes: int = 60) -> FlowGraph:
    """BFS a flow from ``roots``: expand programs through control edges (to ``depth``), attach each
    program's data and screens as leaves. ``max_nodes`` bounds the diagram (sets ``truncated``)."""
    anchor = roots[0].qualified_name if roots else ""
    fg = FlowGraph(anchor=anchor)
    ids: dict[str, str] = {}          # entity_id → short mermaid id
    node_of: dict[str, FlowNode] = {}
    edge_keys: set[tuple[str, str, str]] = set()

    def add_node(ent) -> str:
        nid = _node_id(ent.id, ids)
        if ent.id not in node_of:
            node_of[ent.id] = FlowNode(nid, ent.kind.value, ent.name, ent.provenance.path)
            fg.nodes.append(node_of[ent.id])
        return nid

    def add_edge(src_ent, dst_ent, label: str) -> None:
        key = (src_ent.id, dst_ent.id, label)
        if key in edge_keys:
            return
        edge_keys.add(key)
        fg.edges.append(FlowEdge(ids[src_ent.id], ids[dst_ent.id], label))

    for r in roots:
        add_node(r)
    frontier: list[tuple] = [(r, 0) for r in roots]
    visited: set[str] = set()
    while frontier:
        ent, d = frontier.pop(0)
        if ent.id in visited:
            continue
        visited.add(ent.id)
        if len(fg.nodes) >= max_nodes:
            fg.truncated = True
            break
        # data + screen leaves (not expanded further)
        for rel in graph.out_relationships(ent.id, _DATA_EDGES + _SCREEN_EDGES):
            tgt = graph.get_entity(rel.dst_id)
            if tgt is not None and not tgt.attributes.get("missing"):
                if len(fg.nodes) >= max_nodes and tgt.id not in node_of:
                    fg.truncated = True
                    continue
                add_node(tgt)
                add_edge(ent, tgt, rel.type.value)
        # control edges → programs (expand within depth)
        if d < depth:
            for rel in graph.out_relationships(ent.id, _CONTROL_EDGES):
                tgt = graph.get_entity(rel.dst_id)
                if tgt is not None and not tgt.attributes.get("missing"):
                    if len(fg.nodes) >= max_nodes and tgt.id not in node_of:
                        fg.truncated = True
                        continue
                    add_node(tgt)
                    add_edge(ent, tgt, rel.type.value)
                    frontier.append((tgt, d + 1))
    return fg


def resolve_roots(graph: GraphStore, anchor) -> list:
    """The starting programs/triggers for a flow: a capability expands to its member programs;
    anything else is its own root."""
    if anchor.kind == EntityType.BUSINESS_CAPABILITY:
        roots = []
        for rel in graph.in_relationships(anchor.id, [RelationType.IMPLEMENTS_CAPABILITY]):
            prog = graph.get_entity(rel.src_id)
            if prog is not None and not prog.attributes.get("missing"):
                roots.append(prog)
        return roots or [anchor]
    return [anchor]


__all__ = ["FlowGraph", "FlowNode", "FlowEdge", "build_flow", "resolve_roots"]
