"""Symbol resolution: turn a free-text symbol reference into ranked graph entities.

Shared by hybrid retrieval, impact analysis, and the MCP ``find_symbol`` tool so all surfaces resolve
names identically.
"""

from __future__ import annotations

from ..core.entities import SYMBOL_KINDS, Entity, EntityType
from ..core.interfaces import GraphStore


def resolve_symbol(
    graph: GraphStore, query: str, kind: EntityType | None = None, limit: int = 25
) -> list[Entity]:
    """Resolve *query* to candidate entities, best match first.

    Resolution order: exact name/qualified-name → dotted-suffix (``Class.method``) → substring.
    Symbol-like kinds are preferred over files/modules of the same name.
    """
    q = query.strip()
    if not q:
        return []
    seen: set[str] = set()
    ranked: list[tuple[int, int, Entity]] = []

    def add(entity: Entity, priority: int) -> None:
        if entity.id in seen:
            return
        if kind is not None and entity.kind != kind:
            return
        seen.add(entity.id)
        kind_rank = 0 if entity.kind in SYMBOL_KINDS else 1
        ranked.append((priority, kind_rank * 1000 + len(entity.qualified_name), entity))

    # 1. exact name / qualified name
    for entity in graph.find_by_name(q, exact=True):
        add(entity, 0)
    # 2. dotted suffix, e.g. "PricingCalculator.calculate"
    if "." in q:
        last = q.split(".")[-1]
        for entity in graph.find_by_name(last, exact=True):
            if entity.qualified_name == q or entity.qualified_name.endswith("." + q):
                add(entity, 1)
    # 3. fuzzy substring
    if len(ranked) < limit:
        for entity in graph.find_by_name(q, exact=False):
            add(entity, 2)

    ranked.sort(key=lambda t: (t[0], t[1]))
    return [entity for _, _, entity in ranked[:limit]]


def resolve_one(graph: GraphStore, query: str, kind: EntityType | None = None) -> Entity | None:
    matches = resolve_symbol(graph, query, kind=kind, limit=1)
    return matches[0] if matches else None


__all__ = ["resolve_symbol", "resolve_one"]
