"""Canonical entity types and the :class:`Entity` record.

The entity taxonomy is deliberately broader than "code symbols" so the same graph can absorb
tests, data, runtime/config, ownership, business/domain, and legacy-modernization facts.
See ``docs/canonical-schema.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .provenance import Provenance


class EntityType(str, Enum):
    """All canonical node kinds. Value is a stable lowercase slug used in ids and storage."""

    # --- Structural (code) ---
    REPOSITORY = "repository"
    DIRECTORY = "directory"
    FILE = "file"
    MODULE = "module"
    PACKAGE = "package"
    SYMBOL = "symbol"
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    ENUM = "enum"
    VARIABLE = "variable"
    IMPORT = "import"
    TYPE = "type"

    # --- Testing ---
    TEST = "test"
    TEST_SUITE = "test_suite"

    # --- Data ---
    DATABASE_TABLE = "database_table"
    DATABASE_COLUMN = "database_column"
    DATASET = "dataset"          # z/OS dataset / VSAM file (JCL DD DSN=, COBOL ASSIGN TO)
    QUERY = "query"
    DTO = "dto"

    # --- Runtime / config ---
    CONFIG_KEY = "config_key"
    FEATURE_FLAG = "feature_flag"
    API_ENDPOINT = "api_endpoint"
    JOB = "job"
    QUEUE = "queue"
    TOPIC = "topic"
    LOG_EVENT = "log_event"
    COMPONENT = "component"

    # --- Ownership / evolution ---
    COMMIT = "commit"
    AUTHOR = "author"
    TICKET = "ticket"
    TEAM = "team"

    # --- Business / domain ---
    BUSINESS_CAPABILITY = "business_capability"
    USER_FLOW = "user_flow"
    REPORT = "report"
    SERVICE = "service"

    # --- Legacy modernization ---
    LEGACY_PROGRAM = "legacy_program"
    COPYBOOK = "copybook"
    JCL_JOB = "jcl_job"
    PARAGRAPH = "paragraph"
    TRANSACTION_CODE = "transaction_code"
    SCREEN = "screen"

    # --- Documentation ---
    DOC_SECTION = "doc_section"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Entity kinds that represent a callable/definable code symbol.
SYMBOL_KINDS: frozenset[EntityType] = frozenset(
    {
        EntityType.SYMBOL,
        EntityType.FUNCTION,
        EntityType.METHOD,
        EntityType.CLASS,
        EntityType.INTERFACE,
        EntityType.ENUM,
        EntityType.VARIABLE,
        EntityType.TYPE,
        EntityType.TEST,
        # mainframe: programs/copybooks/jobs/transactions are first-class symbols so
        # resolve_symbol prefers them over same-named FILE/MODULE entities
        EntityType.LEGACY_PROGRAM,
        EntityType.COPYBOOK,
        EntityType.PARAGRAPH,
        EntityType.JCL_JOB,
        EntityType.TRANSACTION_CODE,
    }
)

#: Entity kinds that are callable (can appear on either side of a CALLS edge).
CALLABLE_KINDS: frozenset[EntityType] = frozenset(
    {EntityType.FUNCTION, EntityType.METHOD, EntityType.TEST,
     EntityType.LEGACY_PROGRAM, EntityType.PARAGRAPH}
)

#: Entity kinds that behave like containers (files, dirs, modules).
CONTAINER_KINDS: frozenset[EntityType] = frozenset(
    {
        EntityType.REPOSITORY,
        EntityType.DIRECTORY,
        EntityType.FILE,
        EntityType.MODULE,
        EntityType.PACKAGE,
        EntityType.CLASS,
    }
)


@dataclass
class Entity:
    """A canonical graph node.

    Equality and hashing are based solely on :attr:`id` so entities can be de-duplicated across
    extractors and used as dict keys cheaply.
    """

    id: str
    kind: EntityType
    name: str
    qualified_name: str
    provenance: Provenance
    attributes: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Entity) and other.id == self.id

    # -- serialization ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "provenance": self.provenance.to_dict(),
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entity:
        return cls(
            id=data["id"],
            kind=EntityType(data["kind"]),
            name=data["name"],
            qualified_name=data.get("qualified_name", data["name"]),
            provenance=Provenance.from_dict(data.get("provenance", {"repo_id": ""})),
            attributes=dict(data.get("attributes", {})),
        )

    # -- convenience --------------------------------------------------------
    @property
    def path(self) -> str:
        return self.provenance.path

    @property
    def language(self) -> str | None:
        return self.attributes.get("language")

    def is_symbol(self) -> bool:
        return self.kind in SYMBOL_KINDS

    def is_callable(self) -> bool:
        return self.kind in CALLABLE_KINDS


__all__ = [
    "EntityType",
    "Entity",
    "SYMBOL_KINDS",
    "CALLABLE_KINDS",
    "CONTAINER_KINDS",
]
