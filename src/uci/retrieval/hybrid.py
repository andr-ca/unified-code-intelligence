"""Hybrid, graph-first retrieval.

Combines symbol, keyword, semantic, graph-expansion, file-proximity, and churn signals via RRF.
Works with zero embeddings (semantic signal simply contributes nothing). Every hit explains which
signals fired and why.
"""

from __future__ import annotations

from collections import defaultdict

from ..config import Config
from ..core.entities import Entity, EntityType
from ..core.ids import repo_id as make_repo_id
from ..core.interfaces import EmbeddingProvider, GraphStore, MetadataStore, VectorStore
from ..core.normalize import looks_like_identifier, tokenize
from ..core.relationships import DEPENDENCY_LIKE, RESOLVED_LEVELS, RelationType
from .fusion import reciprocal_rank_fusion
from .symbols import resolve_symbol
from .types import RetrievalHit

_REASONS = {
    "symbol": "Name matches the query symbol",
    "keyword": "Lexical/keyword match on code or docs",
    "semantic": "Semantically similar to the query",
    "lexical-hash": "Lexical (hash) similarity to the query",
    "graph": "Connected in the code graph to a top match",
    "proximity": "Defined near a top match (same file)",
    "churn": "Recently changed (elevated risk)",
}
_SIGNAL_PRIORITY = ["symbol", "semantic", "lexical-hash", "keyword", "graph", "proximity", "churn"]


class HybridRetriever:
    def __init__(
        self,
        config: Config,
        graph: GraphStore,
        metadata: MetadataStore,
        vectors: VectorStore,
        embedder: EmbeddingProvider,
    ) -> None:
        self.config = config
        self.graph = graph
        self.metadata = metadata
        self.vectors = vectors
        self.embedder = embedder
        self.repo_id = make_repo_id(config.repo_path.name or "repo", str(config.repo_path))
        # honest signal label: hash embeddings contribute "lexical-hash", real models "semantic"
        self._sem_signal = getattr(embedder, "signal_name", "semantic")

    # -- public -------------------------------------------------------------
    def search(
        self, query: str, top_k: int = 10, kinds: list[EntityType] | None = None
    ) -> list[RetrievalHit]:
        symbol_ids = self._symbol_signal(query)
        keyword_ids = self._keyword_signal(query)
        semantic_ids = self._semantic_signal(query)

        seeds = _dedupe(symbol_ids[:5] + semantic_ids[:5] + keyword_ids[:5])
        graph_ids, graph_paths = self._graph_signal(seeds)
        proximity_ids = self._proximity_signal(seeds)

        ranked = {
            "symbol": symbol_ids,
            "keyword": keyword_ids,
            self._sem_signal: semantic_ids,
            "graph": graph_ids,
            "proximity": proximity_ids,
        }
        weights = self._weights(query)
        scores, membership = reciprocal_rank_fusion(ranked, weights, self.config.rrf_k)
        scores = self._apply_churn(scores, membership, weights)

        hits: list[RetrievalHit] = []
        kind_set = set(kinds) if kinds else None
        for entity_id, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
            entity = self.graph.get_entity(entity_id)
            if entity is None:
                continue
            if entity.attributes.get("missing"):
                continue  # placeholder stubs are not real code — never return them from search (§12.4)
            if kind_set and entity.kind not in kind_set:
                continue
            signals = membership.get(entity_id, [])
            hit = RetrievalHit.from_entity(
                entity, score, signals,
                reason=self._reason(signals, entity_id, graph_paths),
                confidence=round(min(0.99, 0.5 + 0.12 * len(signals)), 2),
                relationship_path=graph_paths.get(entity_id, []),
            )
            hits.append(hit)
            if len(hits) >= top_k:
                break
        return hits

    # -- signals ------------------------------------------------------------
    def _symbol_signal(self, query: str) -> list[str]:
        return [e.id for e in resolve_symbol(self.graph, query, limit=20)]

    def _keyword_signal(self, query: str) -> list[str]:
        q_tokens = set(tokenize(query))
        if not q_tokens:
            return []
        scored: dict[str, float] = defaultdict(float)
        # entities: match name / qualified name / docstring tokens
        for entity in self.graph.entities(repo_id=self.repo_id):
            tokens = set(tokenize(entity.name) + tokenize(entity.qualified_name))
            doc = entity.attributes.get("docstring", "")
            if doc:
                tokens |= set(tokenize(doc))
            overlap = len(q_tokens & tokens)
            if overlap:
                scored[entity.id] += overlap + (2.0 if entity.name.lower() in q_tokens else 0.0)
        # chunk text: BM25 via FTS5 when available (comments and bodies, not just identifiers);
        # falls back to the stored-token overlap scan on SQLite builds without fts5
        fts = self.metadata.search_text(self.repo_id, query, limit=30)
        if fts is not None:
            if fts:
                best = max(score for _cid, score in fts) or 1.0
                for chunk_id, score in fts:
                    chunk = self.metadata.get_chunk(chunk_id)
                    entity_id = chunk.get("entity_id") if chunk else None
                    if entity_id:
                        scored[entity_id] += 2.0 * (score / best if best > 0 else 0.0)
        else:
            for chunk in self.metadata.iter_chunks(self.repo_id):
                entity_id = chunk.get("entity_id")
                if not entity_id:
                    continue
                overlap = len(q_tokens & set(chunk.get("tokens", [])))
                if overlap:
                    scored[entity_id] += 0.5 * overlap
        return [eid for eid, _ in sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:30]]

    def _semantic_signal(self, query: str) -> list[str]:
        if not getattr(self.embedder, "available", False) or self.vectors.count() == 0:
            return []
        vector = self.embedder.embed_query(query)
        if not vector:
            return []
        results = self.vectors.search(vector, top_k=30, where={"repo_id": self.repo_id})
        entity_ids: list[str] = []
        seen: set[str] = set()
        for chunk_id, _score in results:
            chunk = self.metadata.get_chunk(chunk_id)
            if not chunk:
                continue
            entity_id = chunk.get("entity_id")
            if entity_id and entity_id not in seen:
                seen.add(entity_id)
                entity_ids.append(entity_id)
        return entity_ids

    def _graph_signal(self, seeds: list[str]) -> tuple[list[str], dict[str, list[str]]]:
        ordered: list[str] = []
        paths: dict[str, list[str]] = {}
        rtypes = list(DEPENDENCY_LIKE | {RelationType.DEFINES})
        for seed in seeds:
            seed_entity = self.graph.get_entity(seed)
            if seed_entity is None:
                continue
            for rel, neighbor in self.graph.neighbors(seed, direction="both", rtypes=rtypes):
                # speculative call edges (R4/R5) must not drive graph expansion (recommendations §1.4)
                if rel.type == RelationType.CALLS and rel.attributes.get("resolution") not in RESOLVED_LEVELS:
                    continue
                if neighbor.id in paths:
                    continue
                arrow = "->" if rel.src_id == seed else "<-"
                paths[neighbor.id] = [f"{rel.type.value}{arrow}", seed_entity.name]
                ordered.append(neighbor.id)
        return ordered, paths

    def _proximity_signal(self, seeds: list[str]) -> list[str]:
        seed_paths: set[str] = set()
        for seed in seeds:
            entity = self.graph.get_entity(seed)
            if entity and entity.provenance.path:
                seed_paths.add(entity.provenance.path)
        if not seed_paths:
            return []
        result: list[str] = []
        for entity in self.graph.entities(repo_id=self.repo_id):
            if entity.id in seeds:
                continue
            if entity.provenance.path in seed_paths and entity.is_symbol():
                result.append(entity.id)
        return result[:30]

    def _apply_churn(self, scores, membership, weights) -> dict[str, float]:
        churn_weight = weights.get("churn", 0.0)
        if churn_weight <= 0:
            return scores
        for entity_id in list(scores):
            entity = self.graph.get_entity(entity_id)
            if entity is None or not entity.provenance.path:
                continue
            churn = self.metadata.get_churn(self.repo_id, entity.provenance.path)
            if churn and churn.get("commits"):
                boost = churn_weight * min(1.0, churn["commits"] / 10.0) / self.config.rrf_k
                scores[entity_id] += boost
                membership.setdefault(entity_id, [])
                if "churn" not in membership[entity_id]:
                    membership[entity_id].append("churn")
        return scores

    # -- helpers ------------------------------------------------------------
    def _weights(self, query: str) -> dict[str, float]:
        w = {
            "symbol": self.config.weight_symbol,
            "keyword": self.config.weight_keyword,
            self._sem_signal: self.config.weight_semantic,
            "graph": self.config.weight_graph,
            "proximity": self.config.weight_proximity,
            "churn": self.config.weight_churn,
        }
        # adaptive fusion: prose queries lean semantic; identifier queries keep lexical strong
        if not looks_like_identifier(query) and getattr(self.embedder, "available", False):
            w[self._sem_signal] = w.get(self._sem_signal, self.config.weight_semantic) * 1.3
            w["keyword"] *= 0.8
        return w

    def _reason(self, signals: list[str], entity_id: str, graph_paths: dict[str, list[str]]) -> str:
        for signal in _SIGNAL_PRIORITY:
            if signal in signals:
                if signal == "graph" and entity_id in graph_paths:
                    rel, seed = graph_paths[entity_id]
                    return f"Linked via {rel} {seed}"
                return _REASONS[signal]
        return "Related to the query"


def _dedupe(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


__all__ = ["HybridRetriever"]
