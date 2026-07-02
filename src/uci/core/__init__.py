"""``uci.core`` — the canonical entity/relationship schema, ids, provenance, normalization, and
storage/provider interfaces. This package has zero third-party dependencies.
"""

from __future__ import annotations

from .entities import (
    CALLABLE_KINDS,
    CONTAINER_KINDS,
    SYMBOL_KINDS,
    Entity,
    EntityType,
)
from .ids import entity_id, qualify, relationship_id, repo_id, short_id
from .interfaces import (
    Direction,
    EmbeddingProvider,
    GraphStore,
    MetadataStore,
    VectorStore,
)
from .normalize import (
    looks_like_identifier,
    sanitize_relative_path,
    simple_name,
    split_identifier,
    tokenize,
)
from .provenance import Provenance
from .relationships import (
    CALL_LIKE,
    DATA_FLOW,
    DEPENDENCY_LIKE,
    RESOLVED_LEVELS,
    Relationship,
    RelationType,
)
from .schema import (
    RELATION_SPECS,
    normalize_entity,
    normalize_relation,
    validate_relationship,
)

__all__ = [
    # entities
    "Entity",
    "EntityType",
    "SYMBOL_KINDS",
    "CALLABLE_KINDS",
    "CONTAINER_KINDS",
    # relationships
    "Relationship",
    "RelationType",
    "CALL_LIKE",
    "DEPENDENCY_LIKE",
    "DATA_FLOW",
    "RESOLVED_LEVELS",
    # provenance
    "Provenance",
    # ids
    "entity_id",
    "relationship_id",
    "repo_id",
    "short_id",
    "qualify",
    # normalize
    "tokenize",
    "split_identifier",
    "looks_like_identifier",
    "simple_name",
    "sanitize_relative_path",
    # schema
    "normalize_entity",
    "normalize_relation",
    "validate_relationship",
    "RELATION_SPECS",
    # interfaces
    "GraphStore",
    "VectorStore",
    "MetadataStore",
    "EmbeddingProvider",
    "Direction",
]
