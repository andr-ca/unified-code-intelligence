"""Bolt graph adapter for Memgraph and Neo4j (the first external graph upgrade).

Both databases speak the Bolt protocol via the ``neo4j`` driver, so one adapter serves both. This is
an optional backend: it is only imported when ``UCI_GRAPH_BACKEND=memgraph|neo4j`` and requires
``pip install unified-code-intelligence[memgraph]`` (or ``[neo4j]``). Core code never imports it.

Tests for this adapter are marked ``@pytest.mark.optional_backend`` and skipped unless a server is up.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..config import Config
from ..core.entities import Entity, EntityType
from ..core.interfaces import GraphStore
from ..core.provenance import Provenance
from ..core.relationships import Relationship, RelationType


class BoltGraphStore(GraphStore):
    """Maps canonical entities/relationships onto Cypher nodes/edges.

    Entities become ``(:Entity {id, kind, name, qualified_name, ...})`` nodes; relationships become
    typed edges ``-[:REL {type, ...}]->`` between them. Traversal uses parameterized Cypher.
    """

    def __init__(self, config: Config, flavor: str = "memgraph") -> None:
        try:
            from neo4j import GraphDatabase  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                f"The {flavor!r} backend requires the neo4j driver. Install with "
                f"`pip install unified-code-intelligence[{flavor}]`."
            ) from exc

        settings = config.settings
        uri = settings.get(f"{flavor}_url") or settings.get("neo4j_url") or "bolt://localhost:7687"
        user = settings.get("neo4j_user", "")
        password = settings.get("neo4j_password", "")
        auth = (user, password) if user else None
        self.flavor = flavor
        self._driver = GraphDatabase.driver(uri, auth=auth)
        self._ensure_constraints()

    def _ensure_constraints(self) -> None:  # pragma: no cover - requires server
        with self._driver.session() as session:
            session.run("CREATE INDEX uci_entity_id IF NOT EXISTS FOR (e:Entity) ON (e.id)")

    # -- primitives (require a live server) ---------------------------------
    def add_entity(self, entity: Entity) -> None:  # pragma: no cover - requires server
        p = entity.provenance
        with self._driver.session() as session:
            session.run(
                "MERGE (e:Entity {id:$id}) SET e += $props",
                id=entity.id,
                props={
                    "kind": entity.kind.value, "name": entity.name,
                    "qualified_name": entity.qualified_name, "repo_id": p.repo_id,
                    "path": p.path, "start_line": p.start_line, "end_line": p.end_line,
                },
            )

    def add_relationship(self, rel: Relationship) -> None:  # pragma: no cover - requires server
        with self._driver.session() as session:
            session.run(
                "MATCH (a:Entity {id:$s}),(b:Entity {id:$d}) "
                "MERGE (a)-[r:REL {id:$id}]->(b) SET r.type=$t",
                s=rel.src_id, d=rel.dst_id, id=rel.id, t=rel.type.value,
            )

    def get_entity(self, entity_id: str) -> Entity | None:  # pragma: no cover
        with self._driver.session() as session:
            rec = session.run("MATCH (e:Entity {id:$id}) RETURN e", id=entity_id).single()
        return _record_to_entity(rec["e"]) if rec else None

    def entities(self, kind: EntityType | None = None, repo_id: str | None = None) -> Iterator[Entity]:  # pragma: no cover
        cypher = "MATCH (e:Entity) WHERE 1=1"
        params: dict = {}
        if kind is not None:
            cypher += " AND e.kind=$kind"
            params["kind"] = kind.value
        if repo_id is not None:
            cypher += " AND e.repo_id=$repo"
            params["repo"] = repo_id
        cypher += " RETURN e"
        with self._driver.session() as session:
            for rec in session.run(cypher, **params):
                yield _record_to_entity(rec["e"])

    def relationships(self, rtype: RelationType | None = None) -> Iterator[Relationship]:  # pragma: no cover
        cypher = "MATCH (a)-[r:REL]->(b)"
        params: dict = {}
        if rtype is not None:
            cypher += " WHERE r.type=$t"
            params["t"] = rtype.value
        cypher += " RETURN r, a.id AS s, b.id AS d"
        with self._driver.session() as session:
            for rec in session.run(cypher, **params):
                yield _record_to_rel(rec["r"], rec["s"], rec["d"])

    def out_relationships(self, entity_id: str, rtypes=None) -> list[Relationship]:  # pragma: no cover
        return self._edge_query("(a:Entity {id:$id})-[r:REL]->(b:Entity)", entity_id, rtypes)

    def in_relationships(self, entity_id: str, rtypes=None) -> list[Relationship]:  # pragma: no cover
        return self._edge_query("(b:Entity)-[r:REL]->(a:Entity {id:$id})", entity_id, rtypes)

    def _edge_query(self, pattern: str, entity_id: str, rtypes) -> list[Relationship]:  # pragma: no cover
        cypher = f"MATCH {pattern} "
        params = {"id": entity_id}
        if rtypes:
            cypher += "WHERE r.type IN $types "
            params["types"] = [rt.value for rt in rtypes]
        cypher += "RETURN r, startNode(r).id AS s, endNode(r).id AS d"
        with self._driver.session() as session:
            return [_record_to_rel(rec["r"], rec["s"], rec["d"]) for rec in session.run(cypher, **params)]

    def find_by_name(self, name: str, kind: EntityType | None = None, exact: bool = True) -> list[Entity]:  # pragma: no cover
        op = "=" if exact else "CONTAINS"
        cypher = f"MATCH (e:Entity) WHERE (e.name {op} $n OR e.qualified_name {op} $n)"
        params = {"n": name}
        if kind is not None:
            cypher += " AND e.kind=$k"
            params["k"] = kind.value
        cypher += " RETURN e"
        with self._driver.session() as session:
            return [_record_to_entity(rec["e"]) for rec in session.run(cypher, **params)]

    def clear(self, repo_id: str | None = None) -> None:  # pragma: no cover
        with self._driver.session() as session:
            if repo_id is None:
                session.run("MATCH (e:Entity) DETACH DELETE e")
            else:
                session.run("MATCH (e:Entity {repo_id:$r}) DETACH DELETE e", r=repo_id)

    def close(self) -> None:  # pragma: no cover
        self._driver.close()


def _record_to_entity(node) -> Entity:  # pragma: no cover - requires server
    prov = Provenance(
        repo_id=node.get("repo_id", ""), path=node.get("path", ""),
        start_line=node.get("start_line", 0), end_line=node.get("end_line", 0),
        extractor="bolt",
    )
    return Entity(
        id=node["id"], kind=EntityType(node["kind"]), name=node["name"],
        qualified_name=node.get("qualified_name", node["name"]), provenance=prov, attributes={},
    )


def _record_to_rel(edge, src_id: str, dst_id: str) -> Relationship:  # pragma: no cover
    return Relationship(
        id=edge["id"], type=RelationType(edge["type"]), src_id=src_id, dst_id=dst_id,
        provenance=Provenance(repo_id="", extractor="bolt"), attributes={},
    )


__all__ = ["BoltGraphStore"]
