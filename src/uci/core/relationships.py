"""Canonical relationship types and the :class:`Relationship` record.

These edges are the *non-semantic* backbone of UCI: exact, explainable structure that embeddings
cannot reliably reconstruct. See ``docs/canonical-schema.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .provenance import Provenance


class RelationType(str, Enum):
    """All canonical edge kinds."""

    # structural
    CONTAINS = "contains"
    DEFINES = "defines"
    REFERENCES = "references"
    CALLS = "calls"
    IMPORTS = "imports"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    DEPENDS_ON = "depends_on"

    # data
    READS = "reads"
    WRITES = "writes"
    MAPS_TO = "maps_to"

    # runtime / config
    CONFIGURES = "configures"
    CONTROLS = "controls"
    HANDLES = "handles"
    EMITS = "emits"
    RUNS = "runs"
    SCHEDULES = "schedules"

    # testing
    TESTS = "tests"
    COVERS = "covers"

    # ownership / evolution
    OWNS = "owns"
    CHANGED = "changed"
    RELATES_TO = "relates_to"

    # business / domain
    IMPLEMENTS_CAPABILITY = "implements_capability"
    USES = "uses"

    # legacy modernization
    INVOKES = "invokes"
    CANDIDATE_FOR_MIGRATION = "candidate_for_migration"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Relationship types that participate in call-graph traversal.
CALL_LIKE: frozenset[RelationType] = frozenset({RelationType.CALLS})

#: Relationship types that represent dependency/coupling for impact analysis.
DEPENDENCY_LIKE: frozenset[RelationType] = frozenset(
    {RelationType.CALLS, RelationType.IMPORTS, RelationType.DEPENDS_ON, RelationType.REFERENCES}
)

#: Relationship types that represent data flow.
DATA_FLOW: frozenset[RelationType] = frozenset(
    {RelationType.READS, RelationType.WRITES, RelationType.MAPS_TO}
)

#: Call-edge resolution levels considered "exact enough" (R0-R3) to drive multi-hop traversal.
#: R4 (name-match) and R5 (candidate) are speculative and only appear at depth 1, clearly labeled.
#: ``lsp-verified`` / ``scip`` come from an external oracle confirming an edge (docs/
#: lsp-refactoring-recommendations.md §2) — provable, so they count as resolved.
RESOLVED_LEVELS: frozenset[str] = frozenset(
    {"syntactic", "import-traced", "inherited", "inferred", "lsp-verified", "scip"})


@dataclass
class Relationship:
    """A canonical directed graph edge.

    Equality/hashing is based on :attr:`id`.
    """

    id: str
    type: RelationType
    src_id: str
    dst_id: str
    provenance: Provenance
    attributes: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Relationship) and other.id == self.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "src_id": self.src_id,
            "dst_id": self.dst_id,
            "provenance": self.provenance.to_dict(),
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Relationship:
        return cls(
            id=data["id"],
            type=RelationType(data["type"]),
            src_id=data["src_id"],
            dst_id=data["dst_id"],
            provenance=Provenance.from_dict(data.get("provenance", {"repo_id": ""})),
            attributes=dict(data.get("attributes", {})),
        )


__all__ = [
    "RelationType",
    "Relationship",
    "CALL_LIKE",
    "DEPENDENCY_LIKE",
    "DATA_FLOW",
    "RESOLVED_LEVELS",
]
