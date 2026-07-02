"""Unified Code Intelligence (UCI).

A local-first code-intelligence platform whose source of truth is a canonical knowledge graph.
Embeddings are one retrieval signal; the same graph powers both agents (MCP/API) and humans (web).
"""

from __future__ import annotations

__version__ = "0.1.0"

from .config import Config
from .engine import Engine

__all__ = ["Engine", "Config", "__version__"]
