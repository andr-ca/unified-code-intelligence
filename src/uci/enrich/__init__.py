"""``uci.enrich`` — optional enrichment layer (docs/llm-enrichment.md, lsp-refactoring-recommendations.md).

Never required: the platform is fully functional without any of it. Two families live here, both
optional and gracefully absent:

* **LLM passes** (:class:`Enricher`) — summaries/capabilities/candidates/fields/architecture; every
  fact carries ``extractor="llm:<model>"`` and confidence < 1.0.
* **Edge oracles** (:class:`EdgeSource`) — language servers (LSP) and batch indexes (SCIP) that
  verify/prune/discover graph edges with provable provenance (``lsp-verified`` / ``scip``).
"""

from __future__ import annotations

from .base import Budget, EdgeDelta, EdgeSource
from .enricher import Enricher, EnrichStats
from .llm_client import LlmClient, LlmError

__all__ = ["Enricher", "EnrichStats", "LlmClient", "LlmError",
           "EdgeSource", "EdgeDelta", "Budget"]
