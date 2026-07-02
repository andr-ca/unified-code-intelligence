"""``uci.enrich`` — optional LLM enrichment layer (docs/llm-enrichment.md).

Never required: the platform is fully functional without a configured LLM. All facts written
here carry ``extractor="llm:<model>"`` and confidence < 1.0.
"""

from __future__ import annotations

from .enricher import Enricher, EnrichStats
from .llm_client import LlmClient, LlmError

__all__ = ["Enricher", "EnrichStats", "LlmClient", "LlmError"]
