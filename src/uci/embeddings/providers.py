"""Embedding providers. The default is a dependency-free hash (feature-hashing) embedding so the
whole platform works offline. Real local/cloud providers are optional upgrades behind the same
:class:`~uci.core.interfaces.EmbeddingProvider` protocol.

Crucially, embeddings are *one signal*: when no provider is available, retrieval still works using
keyword, symbol, graph, and proximity signals.
"""

from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request
from collections.abc import Sequence

from ..config import Config
from ..core.normalize import tokenize


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


class NoopEmbeddingProvider:
    """Produces nothing. Selecting this disables the semantic signal entirely."""

    name = "noop"
    model_id = "noop"
    dim = 0
    signal_name = "semantic"

    @property
    def available(self) -> bool:
        return False

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [[] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return []


class LocalEmbeddingProvider:
    """Deterministic feature-hashing embedding (the "signed hashing trick").

    It captures lexical/token overlap without any model download. Not as strong as a trained model,
    but genuinely useful for local semantic recall and fully offline/testable.
    """

    name = "local"
    # Honest labeling: hash overlap is lexical recall, not learned semantics (see recommendations §6.1).
    signal_name = "lexical-hash"

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim
        self.model_id = f"hash-{dim}"

    @property
    def available(self) -> bool:
        return True

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            h = int.from_bytes(digest, "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign
        return _normalize(vec)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class OllamaEmbeddingProvider:
    """Local embeddings via a running Ollama server (stdlib HTTP, no extra dependency).

    Optional upgrade for higher-quality local embeddings (e.g. ``nomic-embed-text``).
    """

    name = "ollama"
    signal_name = "semantic"

    def __init__(self, config: Config) -> None:
        self.model_id = config.embedding_model or "nomic-embed-text"
        self.dim = config.embedding_dim or 768
        self._base = config.settings.get("ollama_base_url", "http://localhost:11434").rstrip("/")

    @property
    def available(self) -> bool:
        try:
            urllib.request.urlopen(f"{self._base}/api/tags", timeout=1.5)  # noqa: S310
            return True
        except (urllib.error.URLError, OSError):  # pragma: no cover - env dependent
            return False

    def _embed(self, text: str) -> list[float]:  # pragma: no cover - requires server
        payload = json.dumps({"model": self.model_id, "prompt": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/api/embeddings", data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        return _normalize([float(x) for x in data.get("embedding", [])])

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:  # pragma: no cover
        return self._embed(text)


def build_embedding_provider(config: Config):
    """Resolve the configured provider by name (falls back to Local if a provider is unavailable)."""
    provider = config.embedding_provider
    if provider == "noop":
        return NoopEmbeddingProvider()
    if provider == "local":
        return LocalEmbeddingProvider(dim=config.embedding_dim or 64)
    if provider == "ollama":
        ollama = OllamaEmbeddingProvider(config)
        if ollama.available:
            return ollama
        # graceful degradation: keep working offline
        return LocalEmbeddingProvider(dim=config.embedding_dim or 64)
    if provider == "openai":
        from .openai_provider import OpenAIEmbeddingProvider  # lazy optional

        return OpenAIEmbeddingProvider(config)
    return LocalEmbeddingProvider(dim=config.embedding_dim or 64)


__all__ = [
    "NoopEmbeddingProvider",
    "LocalEmbeddingProvider",
    "OllamaEmbeddingProvider",
    "build_embedding_provider",
]
