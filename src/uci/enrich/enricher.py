"""LLM enrichment passes (docs/llm-enrichment.md §3).

Every fact written here is labeled ``extractor="llm:<model>"`` with confidence < 1.0; proposed
call targets are validated against the index and use ``resolution="llm-suggested"`` (outside
``RESOLVED_LEVELS`` — candidates stratum only, never multi-hop, completeness stays honest).
Results are cached by source content hash so re-runs only pay for changed files.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core.entities import Entity, EntityType
from ..core.ids import entity_id, relationship_id
from ..core.interfaces import GraphStore, MetadataStore, VectorStore
from ..core.provenance import Provenance
from ..core.relationships import Relationship, RelationType
from .llm_client import LlmClient, LlmError

_SUMMARY_KINDS = (
    EntityType.LEGACY_PROGRAM, EntityType.COPYBOOK, EntityType.JCL_JOB,
    EntityType.TRANSACTION_CODE, EntityType.MODULE,
)
_SOURCE_HEAD_LINES = 80
_SITE_CONTEXT_LINES = 40

_SYS_SUMMARY = (
    "You are a code analyst. Given one artifact and its structural facts, reply with one or two "
    "short sentences stating WHAT it is and WHY it exists (its role for the things that depend "
    "on it). A developer searching the codebase should recognize it from your words. Reply with "
    "the description only — no preamble, no markdown."
)
_SYS_CAPABILITIES = (
    "You are a software architect mapping programs to business capabilities. Reply with STRICT "
    "JSON only: a list of objects {\"name\": str, \"description\": str, \"programs\": [str]}. "
    "Use ONLY program names from the provided inventory. 3-10 capabilities. No markdown."
)
_SYS_CANDIDATES = (
    "You are analyzing a dynamic call site. From the surrounding source, identify which concrete "
    "programs the variable target can hold at runtime. Reply with STRICT JSON only: "
    "{\"candidates\": [str]}. Choose ONLY from the provided program inventory. "
    "If the variable's value is supplied by a caller, a LINKAGE SECTION field, or a COMMAREA — "
    "i.e. no concrete program names are visible in the shown source — you MUST reply "
    "{\"candidates\": []}. Do not guess. No markdown."
)
_SYS_FIELDS = (
    "You are documenting a data structure. For each field in the copybook, give a short business "
    "meaning. Reply with STRICT JSON only: {\"fields\": [{\"name\": str, \"meaning\": str}]}. "
    "No markdown."
)


@dataclass
class EnrichStats:
    summaries: int = 0
    capabilities: int = 0
    candidate_edges: int = 0
    field_dictionaries: int = 0
    cached: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"summaries": self.summaries, "capabilities": self.capabilities,
                "candidate_edges": self.candidate_edges,
                "field_dictionaries": self.field_dictionaries,
                "cached": self.cached, "errors": self.errors[:20]}


class Enricher:
    def __init__(self, config: Config, graph: GraphStore, metadata: MetadataStore,
                 vectors: VectorStore, embedder, repo_id: str,
                 client: LlmClient | None = None) -> None:
        self.config = config
        self.graph = graph
        self.metadata = metadata
        self.vectors = vectors
        self.embedder = embedder
        self.repo_id = repo_id
        self.client = client or LlmClient(config)
        self.stats = EnrichStats()
        self._retriever_cache = None

    def _retriever(self):
        """Hybrid retriever for the agentic candidates loop's rag_search tool (built lazily)."""
        if self._retriever_cache is None:
            from ..retrieval.hybrid import HybridRetriever
            self._retriever_cache = HybridRetriever(
                self.config, self.graph, self.metadata, self.vectors, self.embedder)
        return self._retriever_cache

    # ------------------------------------------------------------------ passes
    def run(self, passes: list[str], limit: int = 200, force: bool = False,
            agentic: bool = False) -> EnrichStats:
        self.agentic = agentic
        if "summaries" in passes:
            self.client.default_tag = "enrich:summaries"  # attribute logged calls to the pass (§2.1)
            self._pass_summaries(limit, force)
        if "capabilities" in passes:
            self.client.default_tag = "enrich:capabilities"
            self._pass_capabilities(force)
        if "candidates" in passes:
            self.client.default_tag = "enrich:candidates"
            self._pass_candidates(limit, force)
        if "fields" in passes:
            self.client.default_tag = "enrich:fields"
            self._pass_fields(limit, force)
        return self.stats

    # -- pass 1: summaries ---------------------------------------------------
    def _pass_summaries(self, limit: int, force: bool) -> None:
        cache = {} if force else self.metadata.get_state(self.repo_id, "enrich:summaries", {}) or {}
        done = 0
        summary_chunks: list[dict] = []
        for kind in _SUMMARY_KINDS:
            for ent in list(self.graph.entities(kind=kind, repo_id=self.repo_id)):
                if done >= limit:
                    break
                if ent.attributes.get("missing") or ent.attributes.get("external"):
                    continue
                if kind == EntityType.MODULE and ent.attributes.get("language") == "config":
                    continue
                src_hash, head = self._source_head(ent)
                if cache.get(ent.id) == src_hash and ent.attributes.get("summary"):
                    self.stats.cached += 1
                    continue
                facts = self._structural_facts(ent)
                try:
                    summary = self.client.complete(
                        _SYS_SUMMARY,
                        f"Artifact: {ent.qualified_name} (kind={ent.kind.value}, "
                        f"language={ent.attributes.get('language', 'n/a')})\n"
                        f"Structural facts:\n{facts}\n\nSource (head):\n{head}",
                        max_tokens=220,  # headroom for thinking models (docs/llm-eval.md)
                    ).strip()
                except LlmError as exc:
                    self.stats.errors.append(f"summaries {ent.qualified_name}: {exc}")
                    continue
                if not summary:
                    continue
                self._write_attr(ent, "summary", summary, pass_name="summaries")
                summary_chunks.append(self._summary_chunk(ent, summary))
                cache[ent.id] = src_hash
                done += 1
                self.stats.summaries += 1
        if summary_chunks:
            self._index_summary_chunks(summary_chunks)
        self.metadata.set_state(self.repo_id, "enrich:summaries", cache)

    def _summary_chunk(self, ent: Entity, summary: str) -> dict:
        return {
            "id": f"summary:{ent.id}", "repo_id": self.repo_id,
            "path": ent.provenance.path, "symbol": ent.qualified_name,
            "kind": "summary", "language": ent.attributes.get("language", ""),
            "start_line": ent.provenance.start_line, "end_line": ent.provenance.start_line,
            "text": f"{ent.qualified_name}: {summary}", "entity_id": ent.id, "tokens": [],
        }

    def _index_summary_chunks(self, chunks: list[dict]) -> None:
        """Summaries enter both retrieval signals: FTS (via chunk upsert) and vectors."""
        upsert = getattr(self.metadata, "upsert_chunks", None)
        if upsert:
            upsert(chunks)
        else:  # pragma: no cover - interface fallback
            for c in chunks:
                self.metadata.upsert_chunk(c)
        if getattr(self.embedder, "available", False):
            vectors = self.embedder.embed_documents([c["text"] for c in chunks])
            items = [(c["id"], v, {"repo_id": self.repo_id, "model": self.embedder.model_id})
                     for c, v in zip(chunks, vectors) if v]
            if items:
                self.vectors.upsert(items)

    # -- pass 2: capabilities --------------------------------------------------
    def _pass_capabilities(self, force: bool) -> None:
        programs = {
            e.qualified_name: e for e in self.graph.entities(repo_id=self.repo_id)
            if e.kind == EntityType.LEGACY_PROGRAM
            and not e.attributes.get("missing") and not e.attributes.get("external")
        }
        if not programs:
            programs = {
                e.qualified_name: e for e in self.graph.entities(
                    kind=EntityType.MODULE, repo_id=self.repo_id)
                if e.attributes.get("language") in ("python", "javascript")
            }
        if not programs:
            return
        inventory = "\n".join(
            f"- {name}: {e.attributes.get('summary', '(no summary)')}"
            for name, e in sorted(programs.items())
        )
        digest = hashlib.sha256(inventory.encode()).hexdigest()[:16]
        if not force and (self.metadata.get_state(self.repo_id, "enrich:capabilities") == digest):
            self.stats.cached += 1
            return
        try:
            data = self.client.complete_json(
                _SYS_CAPABILITIES, f"Program inventory:\n{inventory}", max_tokens=1200)
        except LlmError as exc:
            self.stats.errors.append(f"capabilities: {exc}")
            return
        prov = Provenance(self.repo_id, "", 0, 0, f"llm:{self.client.model}", 0.7)
        for cap in self._json_list(data, "capabilities"):
            if not isinstance(cap, dict):
                continue
            name = str(cap.get("name", "")).strip()
            members = [p for p in cap.get("programs", []) if p in programs]  # validated
            if not name or not members:
                continue
            cap_ent = Entity(
                id=entity_id(EntityType.BUSINESS_CAPABILITY, self.repo_id, "", name),
                kind=EntityType.BUSINESS_CAPABILITY, name=name, qualified_name=name,
                provenance=prov,
                attributes={"description": str(cap.get("description", "")),
                            "llm": {"model": self.client.model, "pass": "capabilities"}},
            )
            self.graph.add_entity(cap_ent)
            for member in members:
                rel_id = relationship_id(RelationType.IMPLEMENTS_CAPABILITY,
                                         programs[member].id, cap_ent.id)
                self.graph.add_relationship(Relationship(
                    id=rel_id, type=RelationType.IMPLEMENTS_CAPABILITY,
                    src_id=programs[member].id, dst_id=cap_ent.id, provenance=prov,
                    attributes={"resolution": "llm-suggested"},
                ))
            self.stats.capabilities += 1
        self.metadata.set_state(self.repo_id, "enrich:capabilities", digest)

    # -- pass 4: dynamic-dispatch candidates -----------------------------------
    def _pass_candidates(self, limit: int, force: bool) -> None:
        unresolved = self.metadata.get_state(self.repo_id, "unresolved_calls", []) or []
        sites = [u for u in unresolved if u.get("reason") == "dynamic-target"][:limit]
        if not sites:
            return
        programs = {
            e.qualified_name.upper(): e for e in self.graph.entities(repo_id=self.repo_id)
            if e.kind == EntityType.LEGACY_PROGRAM
            and not e.attributes.get("missing") and not e.attributes.get("external")
        }
        inventory = ", ".join(sorted(programs))
        cache = {} if force else self.metadata.get_state(self.repo_id, "enrich:candidates", {}) or {}
        for site in sites:
            key = f"{site['path']}:{site['line']}:{site['name']}"
            context = self._site_context(site)
            user = (f"Dynamic call through variable {site['name']} at "
                    f"{site['path']}:{site['line']}.\nProgram inventory: {inventory}\n\n"
                    f"Source context:\n{context}")
            try:
                names, evidence = self._propose_candidates(user)
            except LlmError as exc:
                self.stats.errors.append(f"candidates {key}: {exc}")
                continue
            # cache key = seed context + evidence actually gathered (agentic pulls survive caching)
            ctx_hash = hashlib.sha256((context + json.dumps(evidence, sort_keys=True))
                                      .encode()).hexdigest()[:16]
            if cache.get(key) == ctx_hash:
                self.stats.cached += 1
                continue
            valid = [n for n in (str(n).upper() for n in names) if n in programs]
            caller = self._resolve_caller(site["caller"])
            if caller is not None and valid:
                confidence = min(0.5, round(1.0 / len(valid), 2))
                prov = Provenance(self.repo_id, site["path"], site["line"], site["line"],
                                  f"llm:{self.client.model}", confidence)
                for name in valid:
                    attrs = {"resolution": "llm-suggested", "via": site["name"],
                             "fan_out": len(valid)}
                    if evidence:
                        attrs["evidence"] = evidence
                    self.graph.add_relationship(Relationship(
                        id=relationship_id(RelationType.CALLS, caller.id, programs[name].id,
                                           ordinal=site["line"]),
                        type=RelationType.CALLS, src_id=caller.id, dst_id=programs[name].id,
                        provenance=prov, attributes=attrs,
                    ))
                    self.stats.candidate_edges += 1
            cache[key] = ctx_hash
        self.metadata.set_state(self.repo_id, "enrich:candidates", cache)

    def _propose_candidates(self, user: str) -> tuple[list, dict | None]:
        """One-shot by default; bounded tool-loop when agentic (docs/agentic-enrichment.md).
        Returns (candidate names, evidence digest-or-None)."""
        if not getattr(self, "agentic", False):
            data = self.client.complete_json(_SYS_CANDIDATES, user, max_tokens=300)
            return self._json_list(data, "candidates"), None
        from .tool_loop import ToolLoop
        # give the loop the discovery surfaces that let it locate a copybook holding a dispatch
        # table (rag_search + list_files + COPY-path resolution) — the difference between agentic
        # 0.20 and 1.00 on cross-file resolution (evals/docs/llm-comparison.md §4).
        loop = ToolLoop(self.client, self.graph, self.config.repo_path, self.repo_id,
                        retriever=self._retriever(), metadata=self.metadata, max_tool_calls=4)
        result = loop.run(_SYS_CANDIDATES, user, answer_key="candidates", max_tokens=400)
        evidence = {"digest": result.evidence_digest(), "tool_calls": result.tool_calls,
                    "protocol_errors": result.protocol_errors} if result.transcript else None
        return self._json_list(result.answer, "candidates"), evidence

    # -- pass 5: field dictionaries ---------------------------------------------
    def _pass_fields(self, limit: int, force: bool) -> None:
        cache = {} if force else self.metadata.get_state(self.repo_id, "enrich:fields", {}) or {}
        done = 0
        for ent in list(self.graph.entities(kind=EntityType.COPYBOOK, repo_id=self.repo_id)):
            if done >= limit:
                break
            if ent.attributes.get("missing") or ent.attributes.get("external"):
                continue
            src_hash, source = self._source_head(ent, lines=200)
            if not source or (cache.get(ent.id) == src_hash and ent.attributes.get("data_dictionary")):
                self.stats.cached += bool(source)
                continue
            try:
                data = self.client.complete_json(
                    _SYS_FIELDS, f"Copybook {ent.qualified_name}:\n{source}", max_tokens=900)
            except LlmError as exc:
                self.stats.errors.append(f"fields {ent.qualified_name}: {exc}")
                continue
            fields = {
                str(f.get("name", "")).upper(): str(f.get("meaning", "")).strip()
                for f in self._json_list(data, "fields")
                if isinstance(f, dict) and f.get("name") and f.get("meaning")
            }
            if fields:
                self._write_attr(ent, "data_dictionary", fields, pass_name="fields")
                self.stats.field_dictionaries += 1
            cache[ent.id] = src_hash
            done += 1
        self.metadata.set_state(self.repo_id, "enrich:fields", cache)

    # ------------------------------------------------------------------ helpers
    _CALLER_KINDS = (EntityType.LEGACY_PROGRAM, EntityType.FUNCTION, EntityType.METHOD,
                     EntityType.TEST, EntityType.PARAGRAPH)

    def _resolve_caller(self, qname: str) -> Entity | None:
        """Resolve an unresolved-site caller qname to its *callable* entity — never the
        same-named MODULE/FILE (edges must land where callers/callees queries look)."""
        candidates = [e for e in self.graph.find_by_name(qname.split(".")[-1])
                      if e.qualified_name == qname]
        for kind in self._CALLER_KINDS:
            for ent in candidates:
                if ent.kind == kind:
                    return ent
        return candidates[0] if candidates else None

    def _write_attr(self, ent: Entity, key: str, value, pass_name: str) -> None:
        ent.attributes[key] = value
        ent.attributes["llm"] = {"model": self.client.model, "pass": pass_name}
        self.graph.add_entity(ent)  # upsert

    @staticmethod
    def _json_list(data, key: str) -> list:
        """complete_json may return a bare list or a {key: [...]} object — accept both shapes
        so a model that omits the wrapper key never crashes a pass."""
        if isinstance(data, dict):
            value = data.get(key, [])
            return value if isinstance(value, list) else []
        return data if isinstance(data, list) else []

    def _source_head(self, ent: Entity, lines: int = _SOURCE_HEAD_LINES) -> tuple[str, str]:
        path = ent.provenance.path
        if not path:
            return "", ""
        full = Path(self.config.repo_path) / path
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "", ""
        head = "\n".join(text.splitlines()[:lines])
        return hashlib.sha256(text.encode()).hexdigest()[:16], head

    def _site_context(self, site: dict) -> str:
        full = Path(self.config.repo_path) / site["path"]
        try:
            all_lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        line = int(site.get("line", 1))
        lo = max(0, line - _SITE_CONTEXT_LINES)
        hi = min(len(all_lines), line + 10)
        return "\n".join(all_lines[lo:hi])

    def _structural_facts(self, ent: Entity) -> str:
        facts: list[str] = []
        out = self.graph.out_relationships(ent.id)
        inc = self.graph.in_relationships(ent.id)
        def names(rels, take_dst=True):
            out_names = []
            for rel in rels[:8]:
                other = self.graph.get_entity(rel.dst_id if take_dst else rel.src_id)
                if other:
                    out_names.append(f"{other.name}({rel.type.value})")
            return out_names
        if out:
            facts.append("outgoing: " + ", ".join(names(out)))
        if inc:
            facts.append("incoming: " + ", ".join(names(inc, take_dst=False)))
        return "\n".join(facts) or "(no relationships)"


__all__ = ["Enricher", "EnrichStats"]
