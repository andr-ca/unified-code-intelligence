"""Qdrant vector adapter (the first external vector-store upgrade).

Optional backend: imported only when ``UCI_VECTOR_BACKEND=qdrant``; requires
``pip install unified-code-intelligence[qdrant]``. Core code never imports it. Tests are marked
``@pytest.mark.optional_backend``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ..config import Config
from ..core.interfaces import VectorStore


class QdrantVectorStore(VectorStore):
    """Stores chunk vectors in a Qdrant collection with ``repo_id`` payload filtering."""

    def __init__(self, config: Config, collection: str = "uci_chunks") -> None:
        try:
            from qdrant_client import QdrantClient  # type: ignore
            from qdrant_client.http import models as qmodels  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "The qdrant backend requires qdrant-client. Install with "
                "`pip install unified-code-intelligence[qdrant]`."
            ) from exc

        self._models = qmodels
        url = config.settings.get("qdrant_url", "http://localhost:6333")
        self._client = QdrantClient(url=url)
        self._collection = collection
        self._dim = config.embedding_dim or 64
        self._ensure_collection()

    def _ensure_collection(self) -> None:  # pragma: no cover - requires server
        existing = {c.name for c in self._client.get_collections().collections}
        if self._collection not in existing:
            self._client.create_collection(
                collection_name=self._collection,
                vectors_config=self._models.VectorParams(
                    size=self._dim, distance=self._models.Distance.COSINE
                ),
            )

    def upsert(self, items: Sequence[tuple[str, Sequence[float], dict[str, Any]]]) -> None:  # pragma: no cover
        points = [
            self._models.PointStruct(
                id=abs(hash(chunk_id)) % (2**63),
                vector=list(vec),
                payload={"chunk_id": chunk_id, **meta},
            )
            for chunk_id, vec, meta in items
        ]
        if points:
            self._client.upsert(collection_name=self._collection, points=points)

    def search(self, vector: Sequence[float], top_k: int = 10, where: dict[str, Any] | None = None) -> list[tuple[str, float]]:  # pragma: no cover
        flt = None
        if where and where.get("repo_id"):
            flt = self._models.Filter(
                must=[self._models.FieldCondition(
                    key="repo_id", match=self._models.MatchValue(value=where["repo_id"])
                )]
            )
        hits = self._client.search(
            collection_name=self._collection, query_vector=list(vector),
            limit=top_k, query_filter=flt,
        )
        return [(h.payload["chunk_id"], float(h.score)) for h in hits]

    def delete(self, chunk_ids: Iterable[str]) -> None:  # pragma: no cover
        ids = [abs(hash(c)) % (2**63) for c in chunk_ids]
        if ids:
            self._client.delete(
                collection_name=self._collection,
                points_selector=self._models.PointIdsList(points=ids),
            )

    def clear(self, repo_id: str | None = None) -> None:  # pragma: no cover
        if repo_id is None:
            self._client.delete_collection(self._collection)
            self._ensure_collection()
        else:
            self._client.delete(
                collection_name=self._collection,
                points_selector=self._models.FilterSelector(
                    filter=self._models.Filter(must=[self._models.FieldCondition(
                        key="repo_id", match=self._models.MatchValue(value=repo_id))])
                ),
            )

    def count(self) -> int:  # pragma: no cover
        return int(self._client.count(collection_name=self._collection).count)


__all__ = ["QdrantVectorStore"]
