"""Backend factory: turn declarative :class:`~uci.config.Config` backend names into concrete store
instances. Optional adapters are imported lazily so the local-lite profile never touches a vendor SDK.
"""

from __future__ import annotations

from .config import Config
from .core.interfaces import GraphStore, MetadataStore, VectorStore
from .graph.inmemory import InMemoryGraphStore
from .store.sqlite_backend import (
    SqliteDatabase,
    SQLiteGraphStore,
    SQLiteMetadataStore,
    SQLiteVectorStore,
)


class BackendUnavailableError(RuntimeError):
    """Raised when a selected optional backend is not installed/available."""


def build_metadata_store(config: Config, db: SqliteDatabase) -> MetadataStore:
    backend = config.metadata_backend
    if backend == "sqlite":
        return SQLiteMetadataStore(db)
    if backend == "postgres":
        raise BackendUnavailableError(
            "postgres metadata backend is scaffolded for a later phase; use "
            "UCI_METADATA_BACKEND=sqlite (default) for local-lite."
        )
    raise BackendUnavailableError(f"unknown metadata backend {backend!r}")


def build_graph_store(config: Config, db: SqliteDatabase) -> GraphStore:
    backend = config.graph_backend
    if backend == "sqlite":
        return SQLiteGraphStore(db)
    if backend == "memory":
        store = InMemoryGraphStore()
        # hydrate from SQLite so an in-memory graph still survives restarts
        sqlite_store = SQLiteGraphStore(db)
        store.load(sqlite_store.entities(), sqlite_store.relationships())
        return store
    if backend in ("memgraph", "neo4j"):
        from .graph.bolt_adapter import BoltGraphStore  # lazy: requires `neo4j`

        return BoltGraphStore(config, flavor=backend)
    raise BackendUnavailableError(f"unknown graph backend {backend!r}")


def build_vector_store(config: Config, db: SqliteDatabase) -> VectorStore:
    backend = config.vector_backend
    if backend in ("sqlite", "numpy"):
        return SQLiteVectorStore(db)
    if backend == "qdrant":
        from .store.qdrant_adapter import QdrantVectorStore  # lazy: requires `qdrant-client`

        return QdrantVectorStore(config)
    if backend == "lancedb":
        raise BackendUnavailableError(
            "lancedb vector backend is scaffolded for a later phase; use "
            "UCI_VECTOR_BACKEND=sqlite (default) for local-lite."
        )
    raise BackendUnavailableError(f"unknown vector backend {backend!r}")


__all__ = [
    "BackendUnavailableError",
    "build_metadata_store",
    "build_graph_store",
    "build_vector_store",
]
