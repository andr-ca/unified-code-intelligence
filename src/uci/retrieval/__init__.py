"""``uci.retrieval`` — graph-first hybrid retrieval, impact analysis, and symbol resolution."""

from __future__ import annotations

from .fusion import reciprocal_rank_fusion
from .hybrid import HybridRetriever
from .impact import ImpactAnalyzer
from .symbols import resolve_one, resolve_symbol
from .types import RetrievalHit

__all__ = [
    "HybridRetriever",
    "ImpactAnalyzer",
    "RetrievalHit",
    "resolve_symbol",
    "resolve_one",
    "reciprocal_rank_fusion",
]
