"""Schema metadata: relationship validation and alias normalization.

Extractors (and LLMs) emit inconsistent type names. Like Understand-Anything's schema layer, UCI
maps aliases to canonical types and *warns* (never hard-fails) on unexpected src/dst kinds so new
extractors degrade gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass

from .entities import (
    CALLABLE_KINDS,
    CONTAINER_KINDS,
    SYMBOL_KINDS,
    EntityType,
)
from .relationships import RelationType


@dataclass(frozen=True)
class RelationSpec:
    """Allowed source/target kinds for a relationship type (used for soft validation)."""

    directed: bool
    sources: frozenset[EntityType]
    targets: frozenset[EntityType]


_ANY: frozenset[EntityType] = frozenset(EntityType)

# Soft constraints. Empty frozenset means "any kind".
RELATION_SPECS: dict[RelationType, RelationSpec] = {
    RelationType.CONTAINS: RelationSpec(True, CONTAINER_KINDS, _ANY),
    RelationType.DEFINES: RelationSpec(
        True,
        frozenset({EntityType.FILE, EntityType.MODULE, EntityType.CLASS, EntityType.PACKAGE}),
        SYMBOL_KINDS,
    ),
    RelationType.REFERENCES: RelationSpec(True, _ANY, _ANY),
    RelationType.CALLS: RelationSpec(True, CALLABLE_KINDS, CALLABLE_KINDS | {EntityType.CLASS}),
    RelationType.IMPORTS: RelationSpec(
        True,
        frozenset({EntityType.MODULE, EntityType.FILE, EntityType.PACKAGE}),
        frozenset({EntityType.MODULE, EntityType.PACKAGE, EntityType.SYMBOL, EntityType.IMPORT}),
    ),
    RelationType.EXTENDS: RelationSpec(
        True, frozenset({EntityType.CLASS, EntityType.INTERFACE}), frozenset({EntityType.CLASS, EntityType.INTERFACE})
    ),
    RelationType.IMPLEMENTS: RelationSpec(True, frozenset({EntityType.CLASS}), frozenset({EntityType.INTERFACE})),
    RelationType.DEPENDS_ON: RelationSpec(True, _ANY, _ANY),
    RelationType.READS: RelationSpec(True, CALLABLE_KINDS, _ANY),
    RelationType.WRITES: RelationSpec(True, CALLABLE_KINDS, _ANY),
    RelationType.MAPS_TO: RelationSpec(True, _ANY, _ANY),
    RelationType.CONFIGURES: RelationSpec(True, frozenset({EntityType.CONFIG_KEY}), _ANY),
    RelationType.CONTROLS: RelationSpec(True, frozenset({EntityType.FEATURE_FLAG}), _ANY),
    RelationType.HANDLES: RelationSpec(True, frozenset({EntityType.API_ENDPOINT}), CALLABLE_KINDS | {EntityType.CLASS}),
    RelationType.EMITS: RelationSpec(True, CALLABLE_KINDS, frozenset({EntityType.LOG_EVENT})),
    RelationType.RUNS: RelationSpec(True, frozenset({EntityType.JOB, EntityType.JCL_JOB}), _ANY),
    RelationType.SCHEDULES: RelationSpec(True, _ANY, frozenset({EntityType.JOB})),
    RelationType.TESTS: RelationSpec(True, frozenset({EntityType.TEST, EntityType.TEST_SUITE}), SYMBOL_KINDS | {EntityType.MODULE}),
    RelationType.COVERS: RelationSpec(True, frozenset({EntityType.TEST, EntityType.TEST_SUITE}), frozenset({EntityType.BUSINESS_CAPABILITY})),
    RelationType.OWNS: RelationSpec(True, frozenset({EntityType.AUTHOR, EntityType.TEAM}), _ANY),
    RelationType.CHANGED: RelationSpec(True, frozenset({EntityType.COMMIT}), _ANY),
    RelationType.RELATES_TO: RelationSpec(False, _ANY, _ANY),
    RelationType.IMPLEMENTS_CAPABILITY: RelationSpec(True, _ANY, frozenset({EntityType.BUSINESS_CAPABILITY})),
    RelationType.USES: RelationSpec(True, frozenset({EntityType.USER_FLOW}), _ANY),
    RelationType.INVOKES: RelationSpec(True, frozenset({EntityType.TRANSACTION_CODE}), _ANY),
    RelationType.CANDIDATE_FOR_MIGRATION: RelationSpec(True, _ANY, frozenset({EntityType.SERVICE, EntityType.COMPONENT})),
}


# --- alias normalization ---------------------------------------------------

_ENTITY_ALIASES: dict[str, EntityType] = {
    "func": EntityType.FUNCTION,
    "def": EntityType.FUNCTION,
    "procedure": EntityType.FUNCTION,
    "proc": EntityType.FUNCTION,
    "struct": EntityType.CLASS,
    "record": EntityType.CLASS,
    "protocol": EntityType.INTERFACE,
    "trait": EntityType.INTERFACE,
    "const": EntityType.VARIABLE,
    "field": EntityType.VARIABLE,
    "attr": EntityType.VARIABLE,
    "typedef": EntityType.TYPE,
    "table": EntityType.DATABASE_TABLE,
    "column": EntityType.DATABASE_COLUMN,
    "route": EntityType.API_ENDPOINT,
    "endpoint": EntityType.API_ENDPOINT,
    "flag": EntityType.FEATURE_FLAG,
    "cron": EntityType.JOB,
}

_RELATION_ALIASES: dict[str, RelationType] = {
    "inherits": RelationType.EXTENDS,
    "subclasses": RelationType.EXTENDS,
    "inherit": RelationType.EXTENDS,
    "import": RelationType.IMPORTS,
    "require": RelationType.IMPORTS,
    "include": RelationType.IMPORTS,
    "uses_module": RelationType.IMPORTS,
    "call": RelationType.CALLS,
    "invoke": RelationType.CALLS,
    "reads_from": RelationType.READS,
    "writes_to": RelationType.WRITES,
    "tested_by": RelationType.TESTS,
    "configure": RelationType.CONFIGURES,
    "handled_by": RelationType.HANDLES,
    "owned_by": RelationType.OWNS,
    "depends": RelationType.DEPENDS_ON,
    "reference": RelationType.REFERENCES,
    "ref": RelationType.REFERENCES,
    "contain": RelationType.CONTAINS,
    "define": RelationType.DEFINES,
}


def normalize_entity(kind: str | EntityType) -> EntityType:
    """Map an entity-kind alias/string to a canonical :class:`EntityType`."""
    if isinstance(kind, EntityType):
        return kind
    key = str(kind).strip().lower()
    try:
        return EntityType(key)
    except ValueError:
        return _ENTITY_ALIASES.get(key, EntityType.SYMBOL)


def normalize_relation(rtype: str | RelationType) -> RelationType:
    """Map a relationship alias/string to a canonical :class:`RelationType`."""
    if isinstance(rtype, RelationType):
        return rtype
    key = str(rtype).strip().lower()
    try:
        return RelationType(key)
    except ValueError:
        return _RELATION_ALIASES.get(key, RelationType.RELATES_TO)


def validate_relationship(rtype: RelationType, src_kind: EntityType, dst_kind: EntityType) -> list[str]:
    """Return a list of soft-validation warnings (empty = valid).

    We never raise: unexpected combinations are recorded as warnings so extractors can evolve
    without breaking ingestion.
    """
    spec = RELATION_SPECS.get(rtype)
    if spec is None:
        return [f"unknown relation type {rtype!r}"]
    warnings: list[str] = []
    if spec.sources and src_kind not in spec.sources:
        warnings.append(f"{rtype.value}: unexpected source kind {src_kind.value}")
    if spec.targets and dst_kind not in spec.targets:
        warnings.append(f"{rtype.value}: unexpected target kind {dst_kind.value}")
    return warnings


__all__ = [
    "RelationSpec",
    "RELATION_SPECS",
    "normalize_entity",
    "normalize_relation",
    "validate_relationship",
]
