"""The UCI engine facade — one object that wires stores, embeddings, indexing, retrieval, and
analysis together. Every surface (CLI, MCP, API, web) is a thin adapter over this, so behavior is
identical everywhere (the single-source-of-truth pattern from CodeRAG).
"""

from __future__ import annotations

import json
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
                if rel.attributes.get("pruned"):  # tombstoned by an oracle (LSP/SCIP) — not a real edge
                    continue
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

    # -- optional LLM layer (docs/llm-enrichment.md) -------------------------
    def enrich(self, passes: list[str] | None = None, limit: int = 200,
               force: bool = False, client=None, agentic: bool = False) -> dict:
        """Run the optional LLM enrichment passes. Requires a configured/reachable provider."""
        from .enrich import Enricher, LlmError
        passes = passes or ["summaries", "capabilities", "candidates", "fields", "architecture"]
        try:
            enricher = Enricher(self.config, self.graph, self.metadata, self.vectors,
                                self.embedder, self.repo_id, client=client)
            stats = enricher.run(passes, limit=limit, force=force, agentic=agentic)
        except LlmError as exc:
            return {"ok": False, "tool": "enrich", "error": {"code": "llm", "message": str(exc)}}
        return {"ok": True, "tool": "enrich", "passes": passes, "agentic": agentic,
                "llm": enricher.client.describe(), "stats": stats.to_dict()}

    # -- optional edge oracles: LSP / SCIP (docs/lsp-refactoring-recommendations.md) ----------
    def enrich_edges(self, lsp: list[str] | None = None, scip: list[str] | None = None,
                     budget_seconds: float = 60.0, verify_only: bool = False,
                     complete: bool = False) -> dict:
        """Run optional LSP/SCIP edge sources: **verify** (promote/prune speculative call edges),
        **discover** (unresolved worklist → new edges), and optionally **complete** (type-aware
        references for high-fan-in symbols). No LLM involved; missing toolchains are skipped with
        ``available: false`` rather than failing the run."""
        from .enrich.base import Budget
        from .enrich.lsp_source import LspEdgeSource
        from .enrich.scip_source import ScipSource

        sources = []
        for lang in lsp or []:
            sources.append(LspEdgeSource(lang, self.config.repo_path, settings=self.config.settings))
        for path in scip or []:
            sources.append(ScipSource(path, self.config.repo_path))
        if not sources:
            return {"ok": False, "tool": "enrich_edges",
                    "error": {"code": "no_sources", "message": "pass at least one --lsp or --scip"}}

        worklist = self.metadata.get_state(self.repo_id, "unresolved_calls", []) or []
        symbols = self._high_value_symbols() if complete else []
        reports = []
        for source in sources:
            entry: dict = {"source": source.name, "available": source.available,
                           "promoted": 0, "pruned": 0, "discovered": 0, "queried": 0}
            if source.available:
                try:
                    delta = source.verify(self.graph, self.repo_id, Budget.for_seconds(budget_seconds))
                    if not verify_only:
                        delta.extend(source.discover(self.graph, self.repo_id, worklist,
                                                     Budget.for_seconds(budget_seconds)))
                        if complete:
                            delta.extend(source.complete(self.graph, self.repo_id, symbols,
                                                         Budget.for_seconds(budget_seconds)))
                    for rel in (*delta.promoted, *delta.pruned, *delta.discovered):
                        self.graph.add_relationship(rel)  # upsert by id
                    entry.update(delta.counts())
                finally:
                    source.close()
            reports.append(entry)
        return {"ok": True, "tool": "enrich_edges", "verify_only": verify_only,
                "complete": complete, "sources": reports}

    def _high_value_symbols(self, limit: int = 50) -> list:
        """Highest-fan-in code symbols — the worthwhile targets for LSP ``complete`` (references)."""
        from collections import Counter
        from .core.entities import SYMBOL_KINDS
        from .core.relationships import RelationType
        fan_in: Counter[str] = Counter()
        for rel in self.graph.relationships(RelationType.CALLS):
            fan_in[rel.dst_id] += 1
        out = []
        for eid, _ in fan_in.most_common(limit):
            ent = self.graph.get_entity(eid)
            if ent and ent.kind in SYMBOL_KINDS:
                out.append(ent)
        return out

    def briefing(self, symbol: str, client=None) -> dict:
        """Render the (already-proven) impact pack as a prose modernization briefing."""
        imp = self.impact(symbol)
        if not imp.get("ok"):
            return imp
        from .enrich import LlmClient, LlmError
        try:
            client = client or LlmClient(self.config)
            facts = json.dumps({k: imp.get(k) for k in (
                "target", "callers", "callees", "tests", "config", "data",
                "overrides", "churn", "risk", "completeness")}, indent=1)[:7000]
            prose = client.complete(
                "You write concise migration-readiness briefings for engineers. Use ONLY the "
                "facts in the provided JSON (they carry file/line provenance); cite paths. "
                "Cover: purpose, blast radius, data touched, test safety net, dynamic-dispatch "
                "risk, and a recommended change approach. Plain prose with short headers.",
                f"Impact facts for {symbol}:\n{facts}",
                max_tokens=900,
            )
        except LlmError as exc:
            return {"ok": False, "tool": "briefing", "error": {"code": "llm", "message": str(exc)}}
        return {"ok": True, "tool": "briefing", "symbol": symbol,
                "briefing": prose, "impact": imp}

    def ask(self, question: str, client=None, agentic: bool = False) -> dict:
        """Answer-location routing: is the answer in code, in data (which table/dataset), or
        not in the repo at all? (docs/llm-enrichment.md 'ask').

        ``agentic=True`` runs the router inside the bounded tool-loop so the model can ask the
        RAG follow-up questions, list files, and read specific sources before routing
        (docs/agentic-enrichment.md §3.1). Guardrails are identical in both modes.
        """
        from .enrich import LlmClient, LlmError
        hits = self._retriever().search(question, top_k=8)
        data_inventory = []
        for kind in (EntityType.DATABASE_TABLE, EntityType.DATASET):
            for ent in self.graph.entities(kind=kind, repo_id=self.repo_id):
                readers = [self.graph.get_entity(r.src_id).name
                           for r in self.graph.in_relationships(ent.id, [RelationType.READS])
                           if self.graph.get_entity(r.src_id)]
                writers = [self.graph.get_entity(r.src_id).name
                           for r in self.graph.in_relationships(ent.id, [RelationType.WRITES])
                           if self.graph.get_entity(r.src_id)]
                dd = []
                for rel in self.graph.in_relationships(ent.id, [RelationType.MAPS_TO]):
                    cb = self.graph.get_entity(rel.src_id)
                    if cb and cb.attributes.get("data_dictionary"):
                        dd = list(cb.attributes["data_dictionary"])[:12]
                data_inventory.append({
                    "name": ent.qualified_name, "kind": ent.kind.value,
                    "read_by": readers[:6], "written_by": writers[:6], "fields": dd,
                })
        code_context = "\n".join(
            f"- {h.qualified_name} ({h.kind}): {h.reason}" for h in hits)
        system = (
            "You route a question about a codebase to where its answer lives. Your final reply "
            "must be STRICT JSON: {\"answer_location\": \"code\"|\"data\"|\"not_in_repo\", "
            "\"targets\": [{\"name\": str, \"kind\": str, \"why\": str}], "
            "\"explanation\": str, \"next_step\": str}. Questions about configured/stored "
            "values (product lists, rates, codes) are usually DATA-resident: name the "
            "table/dataset to query. Use ONLY names from the provided context or from tool "
            "results."
        )
        user = (f"Question: {question}\n\nCode matches:\n{code_context or '(none)'}\n\n"
                f"Data inventory (tables/datasets with reader/writer programs):\n"
                f"{json.dumps(data_inventory, indent=1)[:5000]}")
        evidence = None
        try:
            client = client or LlmClient(self.config)
            client.default_tag = "ask:agentic" if agentic else "ask"  # log attribution (§2.1)
            if agentic:
                from .enrich.tool_loop import ToolLoop
                loop = ToolLoop(client, self.graph, self.config.repo_path, self.repo_id,
                                retriever=self._retriever(), metadata=self.metadata,
                                max_tool_calls=4)
                loop_result = loop.run(system, user, answer_key="answer_location",
                                       max_tokens=600)
                data = loop_result.answer
                evidence = {
                    "tool_calls": loop_result.tool_calls,
                    "protocol_errors": loop_result.protocol_errors,
                    "tools_used": [t["request"].get("action") for t in loop_result.transcript],
                    "digest": loop_result.evidence_digest(),
                }
            else:
                data = client.complete_json(system, user, max_tokens=500)
        except LlmError as exc:
            return {"ok": False, "tool": "ask", "error": {"code": "llm", "message": str(exc)}}
        if not isinstance(data, dict):
            data = {}
        # validate targets against the index; attach graph-proven context to data targets
        valid_targets = []
        for t in (data or {}).get("targets", []):
            name = str(t.get("name", ""))
            matches = resolve_symbol(self.graph, name, limit=1)
            if not matches:
                continue
            ent = matches[0]
            target = {"name": ent.qualified_name, "kind": ent.kind.value,
                      "why": str(t.get("why", "")), "entity_id": ent.id}
            if ent.kind in (EntityType.DATABASE_TABLE, EntityType.DATASET):
                target["written_by"] = [
                    self.graph.get_entity(r.src_id).name
                    for r in self.graph.in_relationships(ent.id, [RelationType.WRITES])
                    if self.graph.get_entity(r.src_id)][:6]
                target["read_by"] = [
                    self.graph.get_entity(r.src_id).name
                    for r in self.graph.in_relationships(ent.id, [RelationType.READS])
                    if self.graph.get_entity(r.src_id)][:6]
            valid_targets.append(target)
        location = (data or {}).get("answer_location", "not_in_repo")
        if location != "not_in_repo" and not valid_targets:
            location = "not_in_repo"  # model named things we can't verify — degrade honestly
        out = {"ok": True, "tool": "ask", "question": question,
               "answer_location": location, "targets": valid_targets,
               "explanation": str((data or {}).get("explanation", "")),
               "next_step": str((data or {}).get("next_step", "")),
               "code_matches": [h.to_dict() for h in hits[:5]]}
        if evidence is not None:
            out["evidence"] = evidence
        return out

    def metrics(self) -> dict:
        """Codebase metrics collected at index time: LOC per language (code/comment/blank),
        entry points, cross-file dependencies, call-resolution distribution, hubs."""
        data = self.metadata.get_state(self.repo_id, "code_metrics")
        if not data:
            return {"ok": False, "tool": "get_code_metrics",
                    "error": {"code": "not_indexed", "message": "run `uci index` first"}}
        return {"ok": True, "tool": "get_code_metrics", "metrics": data,
                "index": self._index_status()}

    # -- analysis -----------------------------------------------------------
    def overview(self) -> dict:
        return repo_overview(self.graph, self.metadata, self.repo_id)

    def explain_module(self, query: str) -> dict:
        return explain_module(self.graph, self.metadata, self.repo_id, query)

    def architecture(self) -> dict:
        data = infer_architecture(self.graph, self.repo_id)
        # merge the optional LLM prose overview (enrich 'architecture' pass), if present
        summary = self.metadata.get_state(self.repo_id, "architecture_summary")
        if summary:
            data["summary"] = summary
        return data

    def onboarding(self) -> dict:
        return onboarding_guide(self.graph, self.metadata, self.repo_id)

    def control_flow(self, symbol: str, narrate: bool = False, client=None) -> dict:
        """Block scheme of the logic *inside* a routine: a deterministic control-flow graph
        (decisions, loops, branches, calls) with a Mermaid rendering (docs/control-flow.md).
        Python functions/methods and COBOL programs today. ``narrate`` layers optional LLM
        business-language notes on the blocks (structure is never changed by the LLM)."""
        from pathlib import Path
        from .analysis.cfg import build_cobol_cfg, build_python_cfg, narrate_cfg
        target = resolve_one(self.graph, symbol)
        if target is None:
            return {"ok": False, "tool": "control_flow",
                    "error": {"code": "not_found", "message": symbol}}
        path = target.provenance.path
        py = target.kind in (EntityType.FUNCTION, EntityType.METHOD) and path.endswith(".py")
        cobol = target.kind == EntityType.LEGACY_PROGRAM and path.endswith((".cbl", ".cob", ".cpy"))
        hlasm = target.kind == EntityType.LEGACY_PROGRAM and path.endswith((".asm", ".hlasm", ".mlc"))
        if not (py or cobol or hlasm):
            return {"ok": False, "tool": "control_flow", "error": {"code": "unsupported",
                    "message": f"{target.qualified_name} ({target.kind.value}, {path}): control_flow "
                               "supports Python functions/methods, COBOL and HLASM programs today"}}
        try:
            source = (Path(self.config.repo_path) / path).read_text(encoding="utf-8", errors="replace")
            if py:
                cfg = build_python_cfg(source, target.name, path, target.qualified_name)
            elif cobol:
                cfg = build_cobol_cfg(source, target.name, path)
            else:
                from .analysis.cfg import build_hlasm_cfg
                cfg = build_hlasm_cfg(source, target.name, path)
        except (OSError, ValueError, SyntaxError) as exc:
            return {"ok": False, "tool": "control_flow",
                    "error": {"code": "build_failed", "message": str(exc)}}
        narrated = False
        if narrate:
            try:
                from .enrich import LlmClient
                client = client or LlmClient(self.config)
                client.default_tag = "control_flow:narrate"
                narrate_cfg(cfg, client.complete_json)
                narrated = True
            except Exception as exc:  # narration is best-effort — never fail the diagram
                return {"ok": True, "tool": "control_flow", "narrated": False,
                        "narrate_error": str(exc), **cfg.to_dict()}
        return {"ok": True, "tool": "control_flow", "narrated": narrated, **cfg.to_dict()}

    def flow(self, symbol: str, depth: int = 3) -> dict:
        """Flow-level block scheme (Tier-1): a business flow traced across the graph from an anchor
        (transaction / job / capability / program) — control edges expand the reachable programs,
        data and screens attach as leaves — rendered as Mermaid (docs/control-flow.md §Flow)."""
        from .analysis.flow import build_flow, resolve_roots
        anchor = resolve_one(self.graph, symbol)
        if anchor is None:
            return {"ok": False, "tool": "flow", "error": {"code": "not_found", "message": symbol}}
        roots = resolve_roots(self.graph, anchor)
        if not roots:
            return {"ok": False, "tool": "flow", "error": {"code": "no_roots",
                    "message": f"{anchor.qualified_name} has no programs to trace a flow from"}}
        fg = build_flow(self.graph, roots, depth=depth)
        return {"ok": True, "tool": "flow", "anchor_kind": anchor.kind.value, **fg.to_dict()}

    def flows(self, trigger_depth: int = 4) -> dict:
        """Business-capability flows for the dashboard's Flows tab.

        Each ``BUSINESS_CAPABILITY`` is returned with the programs that implement it
        (``IMPLEMENTS_CAPABILITY``), how it is triggered — the transaction codes / JCL jobs that
        reach those programs via ``CALLS``/``RUNS``/``INVOKES`` (bounded reverse traversal) — and the
        tables it touches (``READS``/``WRITES``).

        Capabilities exist only when the optional LLM enrichment has run (docs/llm-enrichment.md), so
        ``enriched`` is ``False`` when none are present — surfaces prompt for enrichment instead of
        advertising an empty view (recommendations §8.4).
        """
        entry_edges = [RelationType.CALLS, RelationType.RUNS, RelationType.INVOKES]
        trigger_kinds = {EntityType.TRANSACTION_CODE, EntityType.JCL_JOB}
        caps: list[dict] = []
        for cap in self.graph.entities(kind=EntityType.BUSINESS_CAPABILITY, repo_id=self.repo_id):
            programs: list[dict] = []
            triggers: dict[str, dict] = {}
            data: dict[str, dict] = {}
            for rel in self.graph.in_relationships(cap.id, [RelationType.IMPLEMENTS_CAPABILITY]):
                prog = self.graph.get_entity(rel.src_id)
                if prog is None or prog.attributes.get("missing") or prog.attributes.get("external"):
                    continue
                programs.append({**_entity_hit(prog), "summary": prog.attributes.get("summary", "")})
                # triggers: entry points that reach this program (bounded reverse traversal)
                for entity, _hop, _path in self.graph.bfs(
                        prog.id, direction="in", rtypes=entry_edges,
                        max_depth=trigger_depth, limit=60):
                    if entity.kind in trigger_kinds and not entity.attributes.get("missing"):
                        triggers.setdefault(entity.id, _entity_hit(entity))
                # data: tables the program reads/writes directly
                for edge in self.graph.out_relationships(
                        prog.id, [RelationType.READS, RelationType.WRITES]):
                    table = self.graph.get_entity(edge.dst_id)
                    if table is None:
                        continue
                    slot = data.setdefault(table.id, {"hit": _entity_hit(table), "access": set()})
                    slot["access"].add("read" if edge.type == RelationType.READS else "write")
            caps.append({
                "entity_id": cap.id, "name": cap.name,
                "description": cap.attributes.get("description", ""),
                "programs": sorted(programs, key=lambda p: p["name"]),
                "triggers": sorted(triggers.values(), key=lambda t: t["name"]),
                "data": sorted(
                    ({**d["hit"], "access": "/".join(sorted(d["access"]))} for d in data.values()),
                    key=lambda d: d["name"]),
            })
        caps.sort(key=lambda c: c["name"])
        return {"ok": True, "tool": "flows", "enriched": bool(caps), "capabilities": caps}

    def understand(self) -> dict:
        """Composed "how this codebase is organized and how it runs" narrative for the Understand
        tab: what/why, organization (layers + capabilities), execution (entry points → flows),
        key parts (hubs), a reading path, and an honest coverage/blind-spots section.

        Structural-first (no LLM) — every section works from the graph alone; the capability and
        summary layers are added when ``uci enrich`` has run (reflected by ``enriched``).
        """
        from .analysis.coverage import coverage_report
        from .analysis.walkthrough import walkthrough

        overview = self.overview()
        flows = self.flows()
        metrics = self.metrics()
        architecture = self.architecture()
        m = metrics.get("metrics", {}) if metrics.get("ok") else {}
        capabilities = flows.get("capabilities", [])
        enriched = bool(flows.get("enriched"))
        onboarding = self.onboarding()
        gaps = self.gaps()
        return {
            "ok": True, "tool": "understand", "enriched": enriched,
            "summary": {
                "name": overview.get("name", ""),
                "totals": overview.get("totals", {}),
                "languages": overview.get("languages", {}),
                "purpose": [{"name": c["name"], "description": c["description"]}
                            for c in capabilities if c.get("description")][:6],
            },
            "organization": {"layers": architecture.get("layers", []),
                             "edges": architecture.get("edges", []),
                             "summary": architecture.get("summary", {}),
                             "capabilities": capabilities},
            "execution": {"entry_points": m.get("entry_points", {}),
                          "mains": overview.get("entry_points", []),
                          "capabilities": capabilities},
            "walkthrough": walkthrough(self.graph, self.repo_id),
            "key_parts": overview.get("key_symbols", []),
            "reading_path": {"summary": onboarding.get("summary", ""),
                             "steps": onboarding.get("steps", []),
                             "key_concepts": onboarding.get("key_concepts", [])},
            "coverage": {
                "gaps": gaps.get("gaps", []), "gap_count": gaps.get("count", 0),
                "dynamic_call_sites": m.get("dynamic_call_sites", 0),
                "unresolved_call_sites": m.get("unresolved_call_sites", 0),
                "resolution": m.get("call_resolution_distribution", {}),
                **coverage_report(self.graph, self.repo_id),
            },
        }

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
        imp = self._impact()
        return {
            "ok": True,
            "entity": _entity_hit(entity),
            "callers": callers,
            "callees": callees,
            "documentation": imp._documentation(entity),
            "source": imp._source_slice(entity),
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
        has_metrics = self.metadata.get_state(self.repo_id, "code_metrics") is not None
        return {"find_config_dependencies": has_config, "find_data_lineage": has_data,
                "get_code_metrics": has_metrics}

    # -- database browser (read-only inspection of the raw store) -----------
    def db_tables(self) -> dict:
        """Row counts per browsable table, scoped to this repo."""
        out = []
        for table in _DB_TABLES:
            try:
                _, rows = self.db.query_readonly(
                    f"SELECT COUNT(*) FROM {table} WHERE repo_id = ?", (self.repo_id,), limit=1)
                out.append({"table": table, "rows": rows[0][0] if rows else 0})
            except Exception:
                continue
        return {"ok": True, "tool": "db_tables", "tables": out}

    def db_rows(self, table: str, limit: int = 50, offset: int = 0) -> dict:
        """Paginated rows of one allow-listed table, scoped to this repo (cells truncated)."""
        if table not in _DB_TABLES:
            return {"ok": False, "tool": "db_rows",
                    "error": {"code": "bad_table", "message": f"unknown table: {table}"}}
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        _, total_rows = self.db.query_readonly(
            f"SELECT COUNT(*) FROM {table} WHERE repo_id = ?", (self.repo_id,), limit=1)
        total = total_rows[0][0] if total_rows else 0
        columns, rows = self.db.query_readonly(
            f"SELECT * FROM {table} WHERE repo_id = ? LIMIT ? OFFSET ?",
            (self.repo_id, limit, offset), limit=limit)
        return {"ok": True, "tool": "db_rows", "table": table, "columns": columns,
                "rows": [[_db_trunc(v) for v in r] for r in rows],
                "total": total, "limit": limit, "offset": offset}

    def db_query(self, sql: str, limit: int = 500) -> dict:
        """Run a caller-supplied read-only SELECT/WITH query. Read-only is enforced at the SQLite
        level (see ``SqliteDatabase.query_readonly``); cells are truncated for display."""
        cleaned, err = _validate_select(sql)
        if err:
            return {"ok": False, "tool": "db_query", "error": {"code": "bad_sql", "message": err}}
        limit = max(1, min(int(limit), 1000))
        try:
            columns, rows = self.db.query_readonly(cleaned, limit=limit)
        except Exception as exc:
            return {"ok": False, "tool": "db_query", "sql": cleaned,
                    "error": {"code": "query_error", "message": str(exc)}}
        return {"ok": True, "tool": "db_query", "sql": cleaned, "columns": columns,
                "rows": [[_db_trunc(v) for v in r] for r in rows],
                "row_count": len(rows), "capped": len(rows) >= limit}


_DB_TABLES = ("repositories", "files", "entities", "relationships", "chunks",
              "vectors", "state", "git_commits", "git_churn", "gaps")


def _db_trunc(value, limit: int = 200):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "\u2026"


def _validate_select(sql: str) -> tuple[str, str]:
    """Return ``(cleaned_sql, error)``. Only a single SELECT/WITH statement is allowed - a friendly
    guard; the read-only connection in ``query_readonly`` is the real enforcement."""
    cleaned = (sql or "").strip().rstrip(";").strip()
    if not cleaned:
        return "", "empty query"
    if ";" in cleaned:
        return "", "only a single statement is allowed"
    head = cleaned.lstrip("(").lower()
    if not (head.startswith("select") or head.startswith("with")):
        return "", "only read-only SELECT / WITH queries are allowed"
    return cleaned, ""


def _entity_hit(entity) -> dict:
    return {
        "entity_id": entity.id, "kind": entity.kind.value, "name": entity.name,
        "qualified_name": entity.qualified_name, "path": entity.provenance.path,
        "start_line": entity.provenance.start_line, "end_line": entity.provenance.end_line,
        "missing": bool(entity.attributes.get("missing")),
        "external": bool(entity.attributes.get("external")),
        "summary": entity.attributes.get("summary", ""),
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
