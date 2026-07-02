"""In-memory graph store: fast traversal for small repos and the reference implementation the
SQLite backend is validated against by the shared contract tests. Can be hydrated from SQLite.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator

from ..core.entities import Entity, EntityType
from ..core.interfaces import GraphStore
from ..core.relationships import Relationship, RelationType


class InMemoryGraphStore(GraphStore):
    def __init__(self) -> None:
        self._entities: dict[str, Entity] = {}
        self._rels: dict[str, Relationship] = {}
        self._out: dict[str, list[str]] = defaultdict(list)
        self._in: dict[str, list[str]] = defaultdict(list)
        self._by_name: dict[str, set[str]] = defaultdict(set)

    def add_entity(self, entity: Entity) -> None:
        self._entities[entity.id] = entity
        self._by_name[entity.name.lower()].add(entity.id)
        if entity.qualified_name:
            self._by_name[entity.qualified_name.lower()].add(entity.id)

    def add_relationship(self, rel: Relationship) -> None:
        if rel.id in self._rels:
            return
        self._rels[rel.id] = rel
        self._out[rel.src_id].append(rel.id)
        self._in[rel.dst_id].append(rel.id)

    def get_entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def entities(
        self, kind: EntityType | None = None, repo_id: str | None = None
    ) -> Iterator[Entity]:
        for entity in self._entities.values():
            if kind is not None and entity.kind != kind:
                continue
            if repo_id is not None and entity.provenance.repo_id != repo_id:
                continue
            yield entity

    def relationships(self, rtype: RelationType | None = None) -> Iterator[Relationship]:
        for rel in self._rels.values():
            if rtype is None or rel.type == rtype:
                yield rel

    def out_relationships(self, entity_id: str, rtypes=None) -> list[Relationship]:
        rtset = set(rtypes) if rtypes is not None else None
        rels = (self._rels[rid] for rid in self._out.get(entity_id, ()))
        return [r for r in rels if rtset is None or r.type in rtset]

    def in_relationships(self, entity_id: str, rtypes=None) -> list[Relationship]:
        rtset = set(rtypes) if rtypes is not None else None
        rels = (self._rels[rid] for rid in self._in.get(entity_id, ()))
        return [r for r in rels if rtset is None or r.type in rtset]

    def find_by_name(
        self, name: str, kind: EntityType | None = None, exact: bool = True
    ) -> list[Entity]:
        results: list[Entity] = []
        if exact:
            for eid in self._by_name.get(name.lower(), set()):
                entity = self._entities.get(eid)
                if entity and (kind is None or entity.kind == kind):
                    results.append(entity)
        else:
            needle = name.lower()
            for entity in self._entities.values():
                if kind is not None and entity.kind != kind:
                    continue
                if needle in entity.name.lower() or needle in entity.qualified_name.lower():
                    results.append(entity)
        return results

    def clear(self, repo_id: str | None = None) -> None:
        if repo_id is None:
            self._entities.clear()
            self._rels.clear()
            self._out.clear()
            self._in.clear()
            self._by_name.clear()
            return
        keep_entities = {
            eid: e for eid, e in self._entities.items() if e.provenance.repo_id != repo_id
        }
        keep_rels = {
            rid: r for rid, r in self._rels.items() if r.provenance.repo_id != repo_id
        }
        self._entities = keep_entities
        self._rels = keep_rels
        self._out = defaultdict(list)
        self._in = defaultdict(list)
        self._by_name = defaultdict(set)
        for entity in self._entities.values():
            self._by_name[entity.name.lower()].add(entity.id)
            if entity.qualified_name:
                self._by_name[entity.qualified_name.lower()].add(entity.id)
        for rel in self._rels.values():
            self._out[rel.src_id].append(rel.id)
            self._in[rel.dst_id].append(rel.id)

    # -- hydration ----------------------------------------------------------
    def load(self, entities: Iterable[Entity], relationships: Iterable[Relationship]) -> None:
        """Bulk-load from another store (e.g. hydrate from SQLite for fast traversal)."""
        for entity in entities:
            self.add_entity(entity)
        for rel in relationships:
            self.add_relationship(rel)


__all__ = ["InMemoryGraphStore"]
