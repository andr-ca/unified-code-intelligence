"""``uci.graph`` — the canonical knowledge graph and its backends."""

from __future__ import annotations

from .inmemory import InMemoryGraphStore

__all__ = ["InMemoryGraphStore"]
