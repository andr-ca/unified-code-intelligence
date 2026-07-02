"""``uci.embeddings`` — symbol-aware chunking + pluggable embedding providers (embeddings are one
retrieval signal, never required)."""

from __future__ import annotations

from .chunking import Chunk, build_chunks, embed_chunks
from .providers import (
    LocalEmbeddingProvider,
    NoopEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_embedding_provider,
)

__all__ = [
    "Chunk",
    "build_chunks",
    "embed_chunks",
    "NoopEmbeddingProvider",
    "LocalEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "build_embedding_provider",
]
