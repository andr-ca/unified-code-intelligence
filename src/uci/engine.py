"""The UCI engine facade — one object that wires stores, embeddings, indexing, retrieval, and
analysis together. Every surface (CLI, MCP, API, web) is a thin adapter over this, so behavior is
identical everywhere (the single-source-of-truth pattern from CodeRAG).
"""

from __future__ import annotations

from typing import Any

from .analysis.architecture import infer_architecture
from .analysis.onboarding import onboarding_guide
from .analysis.overview import explain_module, repo_overview
from .backends import build_graph_store, build_metadata_store, build_vector_store
from .config import Config
from .core.entities import EntityType
from .core.ids import repo_id as make_repo_id
from .core.normalize import simple_name
from .core.relationships import RESOLVED_LEVELS, RelationType
from .embeddings.providers import build_embedding_provider
from .ingest import git_meta
from .ingest.indexer import Indexer, IndexStats
from .retrieval.hybrid import HybridRetriever
from .retrieval.impact import ImpactAnalyzer
from .retrieval.symbols import resolve_one, resolve_symbol
from .store.sqlite_backend import SqliteDatabase


class Engine:
    def __init__(self, config: Config, db: SqliteDatabase | None = None) -> None:
        self.config = config
        self.db = db or SqliteDatabase(config.db_path)
        self.metadata = build_metadata_store(config, self.db)
        self.graph = build_graph_store(config, self.db)
        self.vectors = build_vector_store(config, self.db)
        self.embedder = build_embedding_provider(config)
        self.repo_id = make_repo_id(config.repo_path.name or "repo", str(config.repo_path))

    @classmethod
    def open(cls, repo_path: str | None = None, overrides: dict[str, Any] | None = None) -> Engine:
        return cls(Config.from_env(repo_path, overrides))

    # -- lifecycle ----------------------------------------------------------
    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> Engine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- indexing -----------------------------------------------------------
    def index(self, full: bool = False) -> IndexStats:
        indexer = Indexer(self.config, self.metadata, self.graph, self.vectors, self.embedder)
        return indexer.index(full=full)

    def is_indexed(self) -> bool:
        return self.metadata.get_repository(self.repo_id) is not None

    # -- retrieval ----------------------------------------------------------
    def _retriever(self) -> HybridRetriever:
        return HybridRetriever(self.config, self.graph, self.metadata, self.vectors, self.embedder)

    def _impact(self) -> ImpactAnalyzer:
        return ImpactAnalyzer(self.config, self.graph, self.metadata)

    def search(self, query: str, top_k: int = 10, kinds: list[EntityType] | None = None) -> dict:
        hits = self._retriever().search(query, top_k=top_k, kinds=kinds)
        return {
            "ok": True,
            "tool": "search_code",
            "query": query,
            "results": [h.to_dict() for h in hits],
            "next_queries": _next_from_hits(hits),
            "index": self._index_status(),
            "stats": {
                "count": len(hits),
                "embeddings": bool(getattr(self.embedder, "available", False)),
                "semantic_signal": getattr(self.embedder, "signal_name", "semantic"),
            },
        }

    def _index_status(self) -> dict:
        """Staleness of the index vs the working tree (recommendations §4.3)."""
        idx = self.metadata.get_state(self.repo_id, "index", {}) or {}
        head = idx.get("head_sha", "")
        behind = git_meta.commits_since(self.config.repo_path, head) if head else 0
        return {
            "generation": idx.get("generation", 0),
            "head_sha": head,
            "indexed_at": idx.get("indexed_at", ""),
            "commits_behind": behind,
        }

    def find_symbol(self, name: str, exact: bool = True, kind: str | None = None) -> dict:
        ekind = EntityType(kind) if kind else None
        matches = resolve_symbol(self.graph, name, kind=ekind, limit=25)
        # never surface placeholder (stub) entities as if they were real definitions (§12.4)
        matches = [m for m in matches if not m.attributes.get("missing")]
        if exact:
            # exact means exact: do not silently fall back to fuzzy matches
            matches = [m for m in matches if name in (m.name, m.qualified_name)]
        return {
            "ok": True, "tool": "find_symbol", "query": name,
            "results": [_entity_hit(m) for m in matches],
        }

    def callers(self, symbol: str, depth: int = 1) -> dict:
        return self._call_graph(symbol, "in", depth, "get_callers")

    def callees(self, symbol: str, depth: int = 1) -> dict:
        return self._call_graph(symbol, "out", depth, "get_callees")

    def _unresolved_naming(self, target) -> list[dict]:
        """Unresolved call sites whose callee name matches the target — possible hidden callers."""
        unresolved = self.metadata.get_state(self.repo_id, "unresolved_calls", []) or []
        name = simple_name(target.qualified_name).lower()
        return [u for u in unresolved if str(u.get("name", "")).lower() == name]

    def _unresolved_from(self, target) -> list[dict]:
        """Unresolved call sites *inside* the target — possible hidden callees (recommendations §12.3)."""
        unresolved = self.metadata.get_state(self.repo_id, "unresolved_calls", []) or []
        return [u for u in unresolved if u.get("caller") == target.qualified_name]

    def _call_graph(self, symbol: str, direction: str, depth: int, tool: str) -> dict:
        target = resolve_one(self.graph, symbol)
        if target is None:
            return {"ok": False, "tool": tool, "error": {"code": "not_found", "message": symbol}}
        rel_fn = self.graph.out_relationships if direction == "out" else self.graph.in_relationships
        arrow = "->" if direction == "out" else "<-"
        results: list[dict] = []
        seen = {target.id}
        frontier: list[tuple[str, int]] = [(target.id, 0)]
        candidate_edges = 0
        while frontier:
            node, hop = frontier.pop(0)
            if hop >= depth:
                continue
            for rel in rel_fn(node, [RelationType.CALLS]):
                resolution = rel.attributes.get("resolution", "")
                is_resolved = resolution in RESOLVED_LEVELS
                # speculative edges (R4/R5) appear only at depth 1, never drive multi-hop (§1.4)
                if hop >= 1 and not is_resolved:
                    continue
                other_id = rel.dst_id if direction == "out" else rel.src_id
                if other_id in seen:
                    continue
                seen.add(other_id)
                other = self.graph.get_entity(other_id)
                if other is None:
                    continue
                if not is_resolved:
                    candidate_edges += 1
                results.append({
                    **_entity_hit(other), "depth": hop + 1, "resolution": resolution,
                    "confidence": rel.provenance.confidence,
                    "relationship_path": [f"CALLS{arrow}"],
                })
                # a node reached via a speculative edge must NOT seed further traversal (§11.1)
                if is_resolved:
                    frontier.append((other_id, hop + 1))
        # hidden callers (callee name == target) for 'in'; hidden callees (caller == target) for 'out'
        unresolved = self._unresolved_naming(target) if direction == "in" else self._unresolved_from(target)
        level = "exact" if candidate_edges == 0 and not unresolved else "partial"
        return {
            "ok": True, "tool": tool, "target": _entity_hit(target), "results": results,
            "completeness": {
                "level": level,
                "candidate_edges": candidate_edges,
                "unresolved_sites": len(unresolved),
            },
        }

    def impact(self, query: str) -> dict:
        data = self._impact().analyze(query)
        if data.get("ok"):
            data["index"] = self._index_status()
        return {"tool": "impact_analysis", "query": query, **data}

    def edit_context(self, query: str) -> dict:
        return {"tool": "retrieve_edit_context", "query": query, **self._impact().edit_context(query)}

    def find_tests_for_symbol(self, symbol: str) -> dict:
        target = resolve_one(self.graph, symbol)
        if target is None:
            return {"ok": False, "tool": "find_tests_for_symbol", "error": {"code": "not_found", "message": symbol}}
        tests = self._impact()._tests(target)  # reuse the analyzer's test discovery
        return {"ok": True, "tool": "find_tests_for_symbol", "target": _entity_hit(target),
                "results": [h.to_dict() for h in tests]}

    def find_config_dependencies(self, query: str) -> dict:
        target = self._impact().resolve_target(query)
        if target is None:
            return {"ok": False, "tool": "find_config_dependencies", "error": {"code": "not_found", "message": query}}
        config = self._impact()._config_for(target)
        return {"ok": True, "tool": "find_config_dependencies", "target": _entity_hit(target),
                "results": [h.to_dict() for h in config]}

    def find_data_lineage(self, query: str) -> dict:
        target = self._impact().resolve_target(query)
        if target is None:
            return {"ok": False, "tool": "find_data_lineage", "error": {"code": "not_found", "message": query}}
        data = self._impact()._data_for(target)
        return {"ok": True, "tool": "find_data_lineage", "target": _entity_hit(target),
                "results": [h.to_dict() for h in data]}

    def gaps(self, kind: str | None = None) -> dict:
        """The acquisition checklist: artifacts referenced but not indexed, ranked by fan-in."""
        records = list(self.metadata.iter_gaps(self.repo_id))
        if kind:
            records = [g for g in records if g.get("artifact_kind") == kind]
        return {"ok": True, "tool": "list_index_gaps", "count": len(records), "gaps": records}

    # -- analysis -----------------------------------------------------------
    def overview(self) -> dict:
        return repo_overview(self.graph, self.metadata, self.repo_id)

    def explain_module(self, query: str) -> dict:
        return explain_module(self.graph, self.metadata, self.repo_id, query)

    def architecture(self) -> dict:
        return infer_architecture(self.graph, self.repo_id)

    def onboarding(self) -> dict:
        return onboarding_guide(self.graph, self.metadata, self.repo_id)

    # -- graph access (for the dashboard graph explorer) -------------------
    def graph_neighborhood(self, entity_id: str, depth: int = 1, limit: int = 60) -> dict:
        root = self.graph.get_entity(entity_id)
        if root is None:
            return {"ok": False, "error": {"code": "not_found", "message": entity_id}}
        nodes = {root.id: _node(root)}
        edges: list[dict] = []
        reached = self.graph.bfs(root.id, direction="both", max_depth=depth, limit=limit)
        for entity, _hop, path in reached:
            nodes[entity.id] = _node(entity)
            rel = path[-1]
            edges.append({"source": rel.src_id, "target": rel.dst_id, "type": rel.type.value})
        return {
            "ok": True, "root": entity_id, "nodes": list(nodes.values()), "edges": edges,
            "truncated": len(reached) >= limit, "limit": limit,
        }

    def entity_detail(self, entity_id: str) -> dict:
        entity = self.graph.get_entity(entity_id)
        if entity is None:
            return {"ok": False, "error": {"code": "not_found", "message": entity_id}}
        callers = [
            _entity_hit(self.graph.get_entity(r.src_id))
            for r in self.graph.in_relationships(entity_id, [RelationType.CALLS])
            if self.graph.get_entity(r.src_id)
        ]
        callees = [
            _entity_hit(self.graph.get_entity(r.dst_id))
            for r in self.graph.out_relationships(entity_id, [RelationType.CALLS])
            if self.graph.get_entity(r.dst_id)
        ]
        return {
            "ok": True,
            "entity": _entity_hit(entity),
            "callers": callers,
            "callees": callees,
            "source": self._impact()._source_slice(entity),
        }

    def default_graph_root(self) -> tuple[str, str]:
        """Return ``(entity_id, label)`` for a good default graph seed (repository, else a module)."""
        for entity in self.graph.entities(kind=EntityType.REPOSITORY, repo_id=self.repo_id):
            return entity.id, entity.name
        for entity in self.graph.entities(kind=EntityType.MODULE, repo_id=self.repo_id):
            return entity.id, entity.qualified_name
        return "", ""

    def capabilities(self) -> dict[str, bool]:
        """Which optional query tools have supporting facts in the current index.

        Lets surfaces avoid advertising a tool that would always return ``[]`` (recommendations §8.4).
        """
        has_config = next(self.graph.entities(kind=EntityType.CONFIG_KEY, repo_id=self.repo_id), None) is not None
        has_data = (
            next(self.graph.entities(kind=EntityType.DATABASE_TABLE, repo_id=self.repo_id), None) is not None
            or next(self.graph.relationships(RelationType.READS), None) is not None
            or next(self.graph.relationships(RelationType.WRITES), None) is not None
            or next(self.graph.relationships(RelationType.MAPS_TO), None) is not None
        )
        return {"find_config_dependencies": has_config, "find_data_lineage": has_data}


def _entity_hit(entity) -> dict:
    return {
        "entity_id": entity.id, "kind": entity.kind.value, "name": entity.name,
        "qualified_name": entity.qualified_name, "path": entity.provenance.path,
        "start_line": entity.provenance.start_line, "end_line": entity.provenance.end_line,
        "missing": bool(entity.attributes.get("missing")),
        "external": bool(entity.attributes.get("external")),
    }


def _node(entity) -> dict:
    return {
        "id": entity.id, "kind": entity.kind.value, "name": entity.name,
        "qualified_name": entity.qualified_name, "path": entity.provenance.path,
        "missing": bool(entity.attributes.get("missing")),
        "external": bool(entity.attributes.get("external")),
    }


def _next_from_hits(hits) -> list[str]:
    out: list[str] = []
    for hit in hits[:3]:
        if hit.kind in ("function", "method", "class", "test"):
            out.append(f"impact_analysis {hit.qualified_name}")
    return out


__all__ = ["Engine"]
