"""EdgeSource — optional fact sources that verify / discover / complete graph edges.

An ``EdgeSource`` is an enrichment adapter over an external oracle: a language server (LSP), a
batch cross-reference index (SCIP), or the platform's own metadata. It follows UCI's adapter
philosophy (docs/lsp-refactoring-recommendations.md §1, §2.2): **optional, lazily loaded,
gracefully absent** — a repo always indexes to a usable graph with zero toolchain; a source only
*upgrades* edge quality when its toolchain exists.

Results are **facts with provenance, not truth** (§2 rule 1). Every edge a source touches is
returned as a fully-formed :class:`~uci.core.relationships.Relationship` carrying
``provenance.extractor = "<source>@<version>"`` and, in its ``attributes``, the mode it came from
(``verified`` / ``discovered``) — so enrichment is re-runnable, diffable, and explainable. Pruning
is never silent: a pruned edge is returned tombstoned (``attributes["pruned"] = True``), never
dropped on the floor (§2 rule 2).
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field

from ..core.relationships import Relationship


@dataclass
class EdgeDelta:
    """The outcome of running a source over the graph — ready-to-upsert relationships.

    ``promoted`` speculative edges the oracle confirmed (resolution upgraded); ``pruned`` edges the
    oracle proved false (tombstoned, kept for completeness accounting); ``discovered`` brand-new
    edges the static extractor missed. All are fully-formed and simply upserted by the caller.
    """

    promoted: list[Relationship] = field(default_factory=list)
    pruned: list[Relationship] = field(default_factory=list)
    discovered: list[Relationship] = field(default_factory=list)
    queried: int = 0

    def __bool__(self) -> bool:
        return bool(self.promoted or self.pruned or self.discovered)

    def extend(self, other: "EdgeDelta") -> None:
        self.promoted += other.promoted
        self.pruned += other.pruned
        self.discovered += other.discovered
        self.queried += other.queried

    def counts(self) -> dict[str, int]:
        return {"promoted": len(self.promoted), "pruned": len(self.pruned),
                "discovered": len(self.discovered), "queried": self.queried}


class EdgeSource(ABC):
    """Base class for optional edge oracles. Subclasses override whichever modes they support and
    set :attr:`name`/:attr:`version` (used to stamp ``extractor`` and key the enrichment cache).

    Every mode is a no-op by default, so a source implements only what its oracle can answer. A
    source that cannot start (missing binary, unreachable server) reports ``available == False`` and
    is skipped with a warning — it never fails the enrich run.
    """

    name: str = "edge-source"
    version: str = "0"

    @property
    def available(self) -> bool:
        """Whether the underlying toolchain is present and startable. Default: assume available."""
        return True

    @property
    def extractor(self) -> str:
        """Provenance label stamped on every fact this source produces."""
        return f"{self.name}@{self.version}"

    def verify(self, graph, repo_id: str, budget: "Budget | None" = None) -> EdgeDelta:
        """Confirm or prune existing *speculative* edges (R4 name-match / R5 candidate)."""
        return EdgeDelta()

    def discover(self, graph, repo_id: str, worklist, budget: "Budget | None" = None) -> EdgeDelta:
        """Find edges the static extractor missed, from an ``unresolved_calls`` worklist."""
        return EdgeDelta()

    def complete(self, graph, repo_id: str, symbols, budget: "Budget | None" = None) -> EdgeDelta:
        """Fill type-aware edges (references/implements/extends) for high-value symbols."""
        return EdgeDelta()

    def close(self) -> None:
        """Release any external process/connection. Safe to call multiple times."""


@dataclass
class Budget:
    """A predictable cost cap for a source run (§2 rule 4): a max number of oracle queries and/or a
    wall-clock deadline. ``spend()`` returns False once exhausted so loops stop cleanly."""

    max_queries: int = 500
    deadline: float = 0.0  # epoch seconds; 0 = no time limit
    spent: int = 0

    def spend(self, n: int = 1) -> bool:
        import time
        if self.deadline and time.time() >= self.deadline:
            return False
        if self.spent + n > self.max_queries:
            return False
        self.spent += n
        return True

    @classmethod
    def for_seconds(cls, seconds: float, max_queries: int = 500) -> "Budget":
        import time
        return cls(max_queries=max_queries, deadline=time.time() + seconds if seconds else 0.0)


__all__ = ["EdgeSource", "EdgeDelta", "Budget"]
