"""Stable, deterministic id generation for entities and relationships.

Deterministic ids make re-indexing idempotent (the same source produces the same id) and let any
surface round-trip an id back into a query. See ``docs/canonical-schema.md`` §1.
"""

from __future__ import annotations

import hashlib
import re

from .entities import EntityType
from .relationships import RelationType

_SANITIZE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _SANITIZE.sub(" ", (text or "").strip())


def repo_id(name: str, root: str) -> str:
    """A stable id for a repository derived from its name and absolute root.

    The absolute root is hashed (not stored) so ids are stable on a machine without leaking the
    full path into every downstream id.
    """
    digest = hashlib.blake2b(root.encode("utf-8"), digest_size=6).hexdigest()
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", name).strip("-").lower() or "repo"
    return f"{slug}-{digest}"


def entity_id(
    kind: EntityType,
    repo: str,
    path: str,
    qualified_name: str,
    disambiguator: str | int | None = None,
) -> str:
    """Compose a readable, stable entity id: ``kind:repo:path:qname[@disambiguator]``."""
    base = f"{kind.value}:{repo}:{path}:{_clean(qualified_name)}"
    if disambiguator is not None:
        base = f"{base}@{disambiguator}"
    return base


def relationship_id(
    rtype: RelationType,
    src_id: str,
    dst_id: str,
    ordinal: int | None = None,
) -> str:
    """Compose a stable relationship id.

    An ordinal disambiguates multiple edges of the same type between the same pair (e.g. two calls
    to the same function on different lines).
    """
    base = f"{rtype.value}:{src_id}->{dst_id}"
    if ordinal is not None:
        base = f"{base}#{ordinal}"
    return base


def short_id(text: str, size: int = 12) -> str:
    """A fixed-length hash id for backends that dislike long/opaque keys."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=size // 2).hexdigest()


def qualify(*parts: str) -> str:
    """Join qualified-name parts with dots, skipping empties."""
    return ".".join(p for p in (part.strip(". ") for part in parts) if p)


__all__ = ["repo_id", "entity_id", "relationship_id", "short_id", "qualify"]
