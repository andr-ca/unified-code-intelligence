"""OpenAI (and OpenAI-compatible) embedding provider — optional cloud upgrade.

Requires ``pip install unified-code-intelligence[openai]`` and ``UCI_OPENAI_API_KEY``. Supports
self-hosted OpenAI-compatible servers via ``UCI_OPENAI_BASE_URL`` (Ollama, vLLM, LM Studio, ...).
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import Config
from ..embeddings.providers import _normalize


class OpenAIEmbeddingProvider:
    name = "openai"
    signal_name = "semantic"

    def __init__(self, config: Config) -> None:
        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "The openai embedding provider requires the openai package. Install with "
                "`pip install unified-code-intelligence[openai]`."
            ) from exc
        api_key = config.settings.get("openai_api_key")
        base_url = config.settings.get("openai_base_url")
        self._client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        self.model_id = config.embedding_model or "text-embedding-3-small"
        self.dim = config.embedding_dim or 1536

    @property
    def available(self) -> bool:  # pragma: no cover - env dependent
        return True

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        resp = self._client.embeddings.create(model=self.model_id, input=list(texts))
        return [_normalize(list(d.embedding)) for d in resp.data]

    def embed_query(self, text: str) -> list[float]:  # pragma: no cover
        return self.embed_documents([text])[0]


__all__ = ["OpenAIEmbeddingProvider"]
