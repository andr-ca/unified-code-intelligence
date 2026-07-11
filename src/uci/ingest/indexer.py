"""The indexing orchestrator: scan → parse → normalize → store graph → chunk → embed → git.

Design notes:
- Parsing is cheap and dependency-free, so every run parses all indexable files and rebuilds the
  repo's graph (guarantees correct cross-file resolution).
- The *expensive* step (embeddings) is incremental: only changed files (by SHA-256 content hash)
  are re-embedded; unchanged files keep their existing vectors. This honors "unchanged files cost
  nothing" for the costly path while keeping the graph always-correct.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..config import Config
from ..core.entities import Entity, EntityType
from ..core.ids import entity_id, relationship_id, repo_id as make_repo_id
from ..core.interfaces import EmbeddingProvider, GraphStore, MetadataStore, VectorStore
from ..core.provenance import Provenance
from ..core.relationships import Relationship, RelationType
from ..embeddings.chunking import build_chunks, embed_chunks
from ..parser.base import ParseResult
from ..parser.registry import get_parser
from . import git_meta
from .docconvert import extract_text
from .graph_builder import FileParse, GraphBuilder
from .hashing import hash_text, read_text
from .langdetect import DOC_CONVERTER_LANGS, is_code, is_doc, module_qname
from .metrics import MetricsCollector
from .scanner import scan


@dataclass
class IndexStats:
    repo_id: str = ""
    files_scanned: int = 0
    files_changed: int = 0
    files_deleted: int = 0
    entities: int = 0
    relationships: int = 0
    chunks: int = 0
    embedded: int = 0
    commits: int = 0
    gaps: int = 0
    doc_sections: int = 0
    doc_links: int = 0
    elapsed_ms: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Indexer:
    def __init__(
        self,
        config: Config,
        metadata: MetadataStore,
        graph: GraphStore,
        vectors: VectorStore,
        embedder: EmbeddingProvider,
    ) -> None:
        self.config = config
        self.metadata = metadata
        self.graph = graph
        self.vectors = vectors
        self.embedder = embedder

    def index(self, full: bool = False) -> IndexStats:
        t0 = time.perf_counter()
        root = self.config.repo_path
        name = root.name or "repo"
        rid = make_repo_id(name, str(root))
        stats = IndexStats(repo_id=rid)

        now = datetime.now(timezone.utc).isoformat()
        self.metadata.upsert_repository(rid, name, str(root), {"created_at": now})

        if full:
            self.graph.clear(rid)
            self.vectors.clear(rid)
            self.metadata.clear(rid)

        stored = {f["path"]: f for f in self.metadata.list_files(rid)}
        existing_chunks: dict[str, list[str]] = defaultdict(list)
        for chunk in self.metadata.iter_chunks(rid):
            existing_chunks[chunk["path"]].append(chunk["id"])

        # -- scan + read + hash --------------------------------------------
        scanned = list(scan(self.config))
        stats.files_scanned = len(scanned)
        scanned_paths = {sf.rel_path for sf in scanned}

        file_parses: list[FileParse] = []
        sources: dict[str, str] = {}
        changed: set[str] = set()
        metrics = MetricsCollector()

        for sf in scanned:
            if sf.language in DOC_CONVERTER_LANGS:
                source = extract_text(sf.abs_path, sf.language, self.config.doc_max_bytes)
            else:
                source = read_text(sf.abs_path, self.config.max_file_bytes)
            if source is None:
                continue
            metrics.add_file(sf.rel_path, sf.language, source)
            digest = hash_text(source)
            prev = stored.get(sf.rel_path)
            if prev is None or prev.get("content_hash") != digest:
                changed.add(sf.rel_path)
            sources[sf.rel_path] = source
            mod = module_qname(sf.rel_path)
            parser = get_parser(sf.language)
            if parser is not None:
                result = parser.parse(source, sf.rel_path, mod)
                stats.errors.extend(f"{sf.rel_path}: {e}" for e in result.errors)
            else:
                result = ParseResult(language=sf.language, module_qname=mod)
            file_parses.append(FileParse(sf.rel_path, sf.language, mod, result))
            # update file record
            self.metadata.upsert_file(rid, sf.rel_path, {
                "language": sf.language, "size": sf.size, "mtime": sf.mtime,
                "content_hash": digest, "indexed_at": now, "meta": {},
            })

        stats.files_changed = len(changed)

        # -- rebuild graph --------------------------------------------------
        builder = GraphBuilder(rid, name, str(root), self.config.gap_external_prefixes)
        entities, relationships = builder.build(file_parses)
        self.graph.clear(rid)
        self.graph.add_entities(entities)
        self.graph.add_relationships(relationships)
        stats.entities = len(entities)
        stats.relationships = len(relationships)
        stats.doc_sections = sum(1 for e in entities if e.kind is EntityType.DOC_SECTION)
        stats.doc_links = sum(1 for r in relationships if r.type is RelationType.DESCRIBES)

        # -- deleted files: purge chunks/vectors/records -------------------
        deleted = set(stored) - scanned_paths
        stats.files_deleted = len(deleted)
        for path in deleted:
            ids = existing_chunks.get(path, [])
            self.vectors.delete(ids)
            self.metadata.delete_chunks_for_file(rid, path)
            self.metadata.delete_file(rid, path)

        # -- embedding-model guard: if the provider/model/dim changed, previously stored vectors
        #    are incompatible. Clear them and force re-embedding of all code files (recommendations §6.2).
        embed_meta = {
            "provider": self.embedder.name,
            "model": getattr(self.embedder, "model_id", ""),
            "dim": getattr(self.embedder, "dim", 0),
        }
        prev_embed_meta = self.metadata.get_state(rid, "embedding_meta")
        if prev_embed_meta and prev_embed_meta != embed_meta:
            self.vectors.clear(rid)
            changed |= {fp.path for fp in file_parses if is_code(fp.path) or is_doc(fp.language)}
            existing_chunks.clear()
        self.metadata.set_state(rid, "embedding_meta", embed_meta)

        # -- chunks + embeddings (incremental) -----------------------------
        for fp in file_parses:
            if not (is_code(fp.path) or is_doc(fp.language)):
                continue
            path = fp.path
            if path not in changed and existing_chunks.get(path):
                # unchanged: keep chunks + vectors as-is (no re-embedding cost)
                stats.chunks += len(existing_chunks[path])
                continue
            # changed (or new): rebuild
            self.vectors.delete(existing_chunks.get(path, []))
            self.metadata.delete_chunks_for_file(rid, path)
            chunks = build_chunks(
                repo_id=rid, path=path, language=fp.language, source=sources[path],
                symbols=fp.result.symbols,
                entity_id_for=lambda sym, p=path: entity_id(sym.kind, rid, p, sym.qualified_name),
                max_chunk_lines=self.config.max_chunk_lines,
                window_lines=self.config.window_lines,
                window_overlap=self.config.window_overlap,
            )
            for chunk in chunks:
                self.metadata.upsert_chunk(chunk.to_dict())
            items = embed_chunks(self.embedder, chunks)
            if items:
                self.vectors.upsert(items)
                stats.embedded += len(items)
            stats.chunks += len(chunks)

        # -- git metadata ---------------------------------------------------
        stats.commits = self._index_git(rid, scanned_paths)

        # -- completeness substrate + index generation ---------------------
        self.metadata.set_state(rid, "unresolved_calls", builder.unresolved_calls)
        prev_index = self.metadata.get_state(rid, "index", {}) or {}
        generation = int(prev_index.get("generation", 0)) + 1
        self.metadata.set_state(rid, "index", {
            "generation": generation,
            "head_sha": git_meta.head_sha(root),
            "indexed_at": now,
        })

        # -- gap registry: never drop a resolution failure -----------------
        # full rebuild each pass -> re-writing the observed gaps auto-closes healed ones
        self.metadata.clear_gaps(rid)
        gap_records = builder.gap_records(generation=generation, first_seen=now)
        for gap in gap_records:
            self.metadata.upsert_gap(rid, gap)
        stats.gaps = len(gap_records)

        # -- codebase metrics: line stats (scan pass) + graph stats (source graph, pre-git) --
        self.metadata.set_state(rid, "code_metrics", metrics.finalize(
            entities, relationships, builder.unresolved_calls, stats.gaps,
        ))

        self.metadata.set_state(rid, "last_index", {
            "at": now, "stats": stats.to_dict(),
            "embedding_provider": self.embedder.name,
            "embedding_signal": getattr(self.embedder, "signal_name", "semantic"),
            "embedding_available": bool(getattr(self.embedder, "available", False)),
        })
        stats.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return stats

    def _index_git(self, rid: str, scanned_paths: set[str]) -> int:
        commits = git_meta.collect_commits(self.config.repo_path)
        if not commits:
            return 0
        churn = git_meta.compute_churn(commits)
        for path, record in churn.items():
            self.metadata.upsert_churn(rid, path, record)

        git_entities: list[Entity] = []
        git_rels: list[Relationship] = []
        authors_seen: set[str] = set()
        owns_seen: set[tuple[str, str]] = set()

        def prov(path: str = "") -> Provenance:
            return Provenance(rid, path, 0, 0, "git", 1.0)

        for commit in commits:
            self.metadata.upsert_git_commit(rid, commit["sha"], commit)
            sha = commit["sha"]
            commit_eid = entity_id(EntityType.COMMIT, rid, "", sha)
            git_entities.append(Entity(
                id=commit_eid, kind=EntityType.COMMIT, name=sha[:8], qualified_name=sha,
                provenance=prov(), attributes={"message": commit["message"], "ts": commit["ts"],
                                               "author": commit["author_email"]},
            ))
            email = commit["author_email"]
            author_eid = entity_id(EntityType.AUTHOR, rid, "", email)
            if email not in authors_seen:
                authors_seen.add(email)
                git_entities.append(Entity(
                    id=author_eid, kind=EntityType.AUTHOR, name=commit["author_name"],
                    qualified_name=email, provenance=prov(), attributes={"email": email},
                ))
            for path in commit["files"]:
                if path not in scanned_paths:
                    continue
                file_eid = entity_id(EntityType.FILE, rid, path, path)
                git_rels.append(Relationship(
                    id=relationship_id(RelationType.CHANGED, commit_eid, file_eid),
                    type=RelationType.CHANGED, src_id=commit_eid, dst_id=file_eid,
                    provenance=prov(path), attributes={},
                ))
                if (email, path) not in owns_seen:
                    owns_seen.add((email, path))
                    git_rels.append(Relationship(
                        id=relationship_id(RelationType.OWNS, author_eid, file_eid),
                        type=RelationType.OWNS, src_id=author_eid, dst_id=file_eid,
                        provenance=prov(path), attributes={},
                    ))
        self.graph.add_entities(git_entities)
        self.graph.add_relationships(git_rels)
        return len(commits)


__all__ = ["Indexer", "IndexStats"]
