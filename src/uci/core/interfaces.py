"""Storage and provider interfaces. No core module depends on a vendor SDK — adapters implement
these and are selected declaratively from :class:`uci.config.Config`.

The abstract base classes provide concrete traversal helpers on top of a small set of primitives so
every backend (in-memory, SQLite, and future Memgraph/Neo4j) shares identical higher-level behavior
and can be exercised by the *same* contract tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from .entities import Entity, EntityType
from .relationships import Relationship, RelationType

Direction = Literal["out", "in", "both"]


# --------------------------------------------------------------------------- embeddings
@runtime_checkable
class EmbeddingProvider(Protocol):
    """Pluggable embedding backend. The default provider is hash-based and needs no ML libs."""

    name: str
    model_id: str
    dim: int

    @property
    def available(self) -> bool:
        """Whether this provider can actually produce meaningful embeddings."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...


# --------------------------------------------------------------------------- vector store
class VectorStore(ABC):
    """Stores chunk vectors and answers nearest-neighbor queries."""

    @abstractmethod
    def upsert(self, items: Sequence[tuple[str, Sequence[float], dict[str, Any]]]) -> None:
        """Insert/replace ``(chunk_id, vector, metadata)`` rows."""

    @abstractmethod
    def search(
        self,
        vector: Sequence[float],
        top_k: int = 10,
        where: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Return ``(chunk_id, similarity)`` pairs, highest similarity first."""

    @abstractmethod
    def delete(self, chunk_ids: Iterable[str]) -> None:
        ...

    @abstractmethod
    def clear(self, repo_id: str | None = None) -> None:
        ...

    @abstractmethod
    def count(self) -> int:
        ...


# --------------------------------------------------------------------------- metadata store
class MetadataStore(ABC):
    """Relational persistence for repositories, files, chunks, hashes, index state, and git."""

    @abstractmethod
    def upsert_repository(self, repo_id: str, name: str, root: str, meta: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_repository(self, repo_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_repositories(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def upsert_file(self, repo_id: str, path: str, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_file(self, repo_id: str, path: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_files(self, repo_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def delete_file(self, repo_id: str, path: str) -> None: ...

    @abstractmethod
    def upsert_chunk(self, chunk: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def iter_chunks(self, repo_id: str | None = None) -> Iterator[dict[str, Any]]: ...

    @abstractmethod
    def delete_chunks_for_file(self, repo_id: str, path: str) -> None: ...

    def search_text(self, repo_id: str, query: str, limit: int = 30) -> list[tuple[str, float]] | None:
        """Lexical full-text chunk search: ``(chunk_id, score)`` best-first. Default ``None``
        signals "no FTS support here" so retrieval falls back to token overlap."""
        return None

    @abstractmethod
    def set_state(self, repo_id: str, key: str, value: Any) -> None: ...

    @abstractmethod
    def get_state(self, repo_id: str, key: str, default: Any = None) -> Any: ...

    @abstractmethod
    def clear(self, repo_id: str | None = None) -> None: ...

    # -- git metadata (concrete defaults; backends may override) ------------
    def upsert_git_commit(self, repo_id: str, sha: str, record: dict[str, Any]) -> None:
        """Record a commit. Default no-op for backends without git support."""

    def iter_git_commits(self, repo_id: str) -> Iterator[dict[str, Any]]:
        return iter(())

    def upsert_churn(self, repo_id: str, path: str, record: dict[str, Any]) -> None:
        """Record per-file churn (commit count, authors, last change)."""

    def get_churn(self, repo_id: str, path: str) -> dict[str, Any] | None:
        return None

    # -- gap registry ("known unknowns"); concrete defaults, backends may override ------------
    def upsert_gap(self, repo_id: str, gap: dict[str, Any]) -> None:
        """Record a missing artifact the index couldn't resolve."""

    def iter_gaps(self, repo_id: str) -> Iterator[dict[str, Any]]:
        return iter(())

    def clear_gaps(self, repo_id: str) -> None:
        """Remove all gap records for a repository (used before re-writing on each index pass)."""


# --------------------------------------------------------------------------- graph store
class GraphStore(ABC):
    """The canonical knowledge graph: entities + directed relationships + traversal.

    Subclasses implement the *primitives* below; the concrete helpers (neighbors, bfs, counts) are
    shared so behavior is identical across backends.
    """

    # -- primitives ---------------------------------------------------------
    @abstractmethod
    def add_entity(self, entity: Entity) -> None: ...

    @abstractmethod
    def add_relationship(self, rel: Relationship) -> None: ...

    @abstractmethod
    def get_entity(self, entity_id: str) -> Entity | None: ...

    @abstractmethod
    def entities(self, kind: EntityType | None = None, repo_id: str | None = None) -> Iterator[Entity]: ...

    @abstractmethod
    def relationships(self, rtype: RelationType | None = None) -> Iterator[Relationship]: ...

    @abstractmethod
    def out_relationships(
        self, entity_id: str, rtypes: Iterable[RelationType] | None = None
    ) -> list[Relationship]: ...

    @abstractmethod
    def in_relationships(
        self, entity_id: str, rtypes: Iterable[RelationType] | None = None
    ) -> list[Relationship]: ...

    @abstractmethod
    def find_by_name(
        self, name: str, kind: EntityType | None = None, exact: bool = True
    ) -> list[Entity]: ...

    @abstractmethod
    def clear(self, repo_id: str | None = None) -> None: ...

    # -- batch convenience --------------------------------------------------
    def add_entities(self, entities: Iterable[Entity]) -> None:
        for entity in entities:
            self.add_entity(entity)

    def add_relationships(self, rels: Iterable[Relationship]) -> None:
        for rel in rels:
            self.add_relationship(rel)

    def has_entity(self, entity_id: str) -> bool:
        return self.get_entity(entity_id) is not None

    # -- shared traversal helpers ------------------------------------------
    def neighbors(
        self,
        entity_id: str,
        direction: Direction = "out",
        rtypes: Iterable[RelationType] | None = None,
    ) -> list[tuple[Relationship, Entity]]:
        """Return ``(relationship, neighbor_entity)`` pairs one hop away."""
        rels: list[Relationship] = []
        if direction in ("out", "both"):
            rels.extend(self.out_relationships(entity_id, rtypes))
        if direction in ("in", "both"):
            rels.extend(self.in_relationships(entity_id, rtypes))
        out: list[tuple[Relationship, Entity]] = []
        for rel in rels:
            other_id = rel.dst_id if rel.src_id == entity_id else rel.src_id
            other = self.get_entity(other_id)
            if other is not None:
                out.append((rel, other))
        return out

    def bfs(
        self,
        start_id: str,
        direction: Direction = "out",
        rtypes: Iterable[RelationType] | None = None,
        max_depth: int = 2,
        limit: int = 200,
    ) -> list[tuple[Entity, int, list[Relationship]]]:
        """Breadth-first traversal returning ``(entity, depth, path_of_relationships)``."""
        rtypes = list(rtypes) if rtypes is not None else None
        seen: set[str] = {start_id}
        queue: deque[tuple[str, int, list[Relationship]]] = deque([(start_id, 0, [])])
        results: list[tuple[Entity, int, list[Relationship]]] = []
        while queue and len(results) < limit:
            node_id, depth, path = queue.popleft()
            if depth >= max_depth:
                continue
            for rel, neighbor in self.neighbors(node_id, direction, rtypes):
                if neighbor.id in seen:
                    continue
                seen.add(neighbor.id)
                new_path = path + [rel]
                results.append((neighbor, depth + 1, new_path))
                queue.append((neighbor.id, depth + 1, new_path))
        return results

    def count_entities(self, kind: EntityType | None = None) -> int:
        return sum(1 for _ in self.entities(kind))

    def count_relationships(self, rtype: RelationType | None = None) -> int:
        return sum(1 for _ in self.relationships(rtype))


__all__ = [
    "Direction",
    "EmbeddingProvider",
    "VectorStore",
    "MetadataStore",
    "GraphStore",
]
