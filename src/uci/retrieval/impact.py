"""Impact analysis and edit-context assembly — the flagship graph-native queries.

Answers "what breaks if I change X?" by traversing the graph (callers, callees, tests, config, data,
churn) rather than guessing with embeddings. Produces structured, explainable packs.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..core.entities import Entity, EntityType
from ..core.ids import repo_id as make_repo_id
from ..core.interfaces import GraphStore, MetadataStore
from ..core.normalize import simple_name, tokenize
from ..core.relationships import RESOLVED_LEVELS, RelationType
from .symbols import resolve_symbol
from .types import RetrievalHit


class ImpactAnalyzer:
    def __init__(self, config: Config, graph: GraphStore, metadata: MetadataStore) -> None:
        self.config = config
        self.graph = graph
        self.metadata = metadata
        self.repo_id = make_repo_id(config.repo_path.name or "repo", str(config.repo_path))

    # -- resolution ---------------------------------------------------------
    def resolve_target(self, query: str) -> Entity | None:
        # allow "path.py:Symbol" form
        if ":" in query and "/" in query.split(":", 1)[0]:
            _, _, symbol = query.partition(":")
            query = symbol.strip() or query
        matches = resolve_symbol(self.graph, query, limit=1)
        if matches:
            return matches[0]
        # fall back to file entity by path
        for entity in self.graph.entities(kind=EntityType.FILE, repo_id=self.repo_id):
            if entity.provenance.path == query or entity.name == query:
                return entity
        return None

    # -- impact pack --------------------------------------------------------
    def analyze(self, query: str) -> dict:
        target = self.resolve_target(query)
        if target is None:
            return {"ok": False, "error": {"code": "not_found", "message": f"symbol not found: {query}"}}

        callers = self._callers(target)
        callees = self._callees(target)
        tests = self._tests(target)
        config = self._config_for(target)
        data = self._data_for(target)
        overrides = self._overrides(target)
        churn = self.metadata.get_churn(self.repo_id, target.provenance.path) or {}
        risk = self._risk(callers, callees, tests, churn)

        caller_resolved, caller_candidates = _stratify(callers)
        callee_resolved, callee_candidates = _stratify(callees)
        unresolved = self._unresolved_for(target)
        callee_unresolved = self._unresolved_from(target)
        completeness = self._completeness(
            simple_name(target.qualified_name), len(caller_resolved),
            len(caller_candidates), len(unresolved), len(callee_unresolved),
        )
        gap_cites = self._gaps_for(target)
        if gap_cites:
            completeness["gaps"] = gap_cites
            names = ", ".join(g["name"] for g in gap_cites)
            completeness["reasons"].append(f"{len(gap_cites)} referenced artifact(s) not indexed: {names}")
            if completeness["level"] == "exact":
                completeness["level"] = "partial"

        return {
            "ok": True,
            "target": _target_dict(target),
            "callers": {
                "resolved": [h.to_dict() for h in caller_resolved],
                "candidates": [h.to_dict() for h in caller_candidates],
                "unresolved": {
                    "count": len(unresolved),
                    "names": sorted({u["name"] for u in unresolved}),
                    "note": f"{len(unresolved)} dynamic/ambiguous call site(s) could not be attributed"
                            if unresolved else "",
                },
            },
            "callees": {
                "resolved": [h.to_dict() for h in callee_resolved],
                "candidates": [h.to_dict() for h in callee_candidates],
                "unresolved": {
                    "count": len(callee_unresolved),
                    "names": sorted({u["name"] for u in callee_unresolved}),
                    "note": f"{len(callee_unresolved)} dynamic call site(s) inside the target"
                            if callee_unresolved else "",
                },
            },
            "tests": [h.to_dict() for h in tests],
            "config": [h.to_dict() for h in config],
            "data": [h.to_dict() for h in data],
            "documentation": self._documentation(target),
            "overrides": [h.to_dict() for h in overrides],
            "churn": {
                "commits": churn.get("commits", 0),
                "authors": churn.get("authors", []),
                "last_changed": churn.get("last_ts", ""),
            },
            "risk": risk,
            "completeness": completeness,
            "next_queries": [
                f"find_tests_for_symbol {target.qualified_name}",
                f"retrieve_edit_context {target.qualified_name}",
            ],
        }

    def _documentation(self, target: Entity) -> list[dict]:
        """Doc sections describing the target (direct, or via its file/module). Never affects risk."""
        out: list[dict] = []
        seen: set[str] = set()
        ids = [target.id]
        for other in self.graph.entities(repo_id=target.provenance.repo_id):  # file/module twins
            if other.provenance.path == target.provenance.path and \
                    other.kind in (EntityType.FILE, EntityType.MODULE) and other.id != target.id:
                ids.append(other.id)
        for eid in ids:
            for rel in self.graph.in_relationships(eid, [RelationType.DESCRIBES]):
                sec = self.graph.get_entity(rel.src_id)
                if sec is None or sec.id in seen or rel.attributes.get("resolution") == "missing":
                    continue
                seen.add(sec.id)
                out.append({
                    "entity_id": sec.id, "heading": sec.attributes.get("heading", sec.name),
                    "path": sec.provenance.path, "start_line": sec.provenance.start_line,
                    "end_line": sec.provenance.end_line,
                    "resolution": rel.attributes.get("resolution", ""),
                    "confidence": rel.provenance.confidence,
                    "context": rel.attributes.get("context", ""),
                })
        out.sort(key=lambda d: -d["confidence"])
        return out[:10]

    def _unresolved_for(self, target: Entity) -> list[dict]:
        """Unresolved call sites whose callee name matches the target (possible hidden callers)."""
        unresolved = self.metadata.get_state(self.repo_id, "unresolved_calls", []) or []
        name = simple_name(target.qualified_name).lower()
        return [u for u in unresolved if str(u.get("name", "")).lower() == name]

    def _unresolved_from(self, target: Entity) -> list[dict]:
        """Unresolved call sites *inside* the target (possible hidden callees) — mirrors get_callees so
        the impact pack and get_callees answer completeness identically (recommendations §13.1)."""
        unresolved = self.metadata.get_state(self.repo_id, "unresolved_calls", []) or []
        return [u for u in unresolved if u.get("caller") == target.qualified_name]

    def _gaps_for(self, target: Entity) -> list[dict]:
        """Gap records (missing artifacts) referenced from the target's file — cited in completeness."""
        path = target.provenance.path
        if not path:
            return []
        out = []
        for gap in self.metadata.iter_gaps(self.repo_id):
            if any(site.get("path") == path for site in gap.get("referencing_sites", [])):
                out.append({"name": gap["name"], "kind": gap["artifact_kind"], "ref_count": gap["ref_count"]})
        return out

    def _completeness(self, name: str, resolved: int, candidates: int, unresolved: int,
                      callee_unresolved: int = 0) -> dict:
        """Compute (not assert) how complete the blast radius is (recommendations §1.5 / §13.1)."""
        reasons: list[str] = []
        if candidates:
            reasons.append(f"{candidates} speculative caller edge(s) (name-match/ambiguous)")
        if unresolved:
            reasons.append(f"{unresolved} unresolved call site(s) naming '{name}'")
        if callee_unresolved:
            reasons.append(f"{callee_unresolved} dynamic call site(s) inside the target")
        if not reasons:
            level = "exact"
        elif resolved == 0 and (candidates or unresolved):
            level = "heuristic"
        else:
            level = "partial"
        return {"level": level, "reasons": reasons}

    def _callers(self, target: Entity) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for rel in self.graph.in_relationships(target.id, [RelationType.CALLS]):
            if rel.attributes.get("pruned"):  # oracle-tombstoned edge — excluded from impact
                continue
            caller = self.graph.get_entity(rel.src_id)
            if caller:
                resolution = rel.attributes.get("resolution", "")
                hit = RetrievalHit.from_entity(
                    caller, 1.0, ["graph"],
                    f"Direct caller of the target ({resolution})" if resolution else "Direct caller of the target",
                    confidence=rel.provenance.confidence,
                    relationship_path=["CALLS<-", target.name],
                )
                hit.resolution = resolution
                hits.append(hit)
        return hits

    def _callees(self, target: Entity) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for rel in self.graph.out_relationships(target.id, [RelationType.CALLS]):
            if rel.attributes.get("pruned"):  # oracle-tombstoned edge — excluded from impact
                continue
            callee = self.graph.get_entity(rel.dst_id)
            if callee:
                resolution = rel.attributes.get("resolution", "")
                hit = RetrievalHit.from_entity(
                    callee, 1.0, ["graph"],
                    f"Called by the target ({resolution})" if resolution else "Called by the target (dependency)",
                    confidence=rel.provenance.confidence,
                    relationship_path=["CALLS->", callee.name],
                )
                hit.resolution = resolution
                hits.append(hit)
        return hits

    def _tests(self, target: Entity) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        seen: set[str] = set()
        # explicit TESTS edges
        for rel in self.graph.in_relationships(target.id, [RelationType.TESTS]):
            test = self.graph.get_entity(rel.src_id)
            if test and test.id not in seen:
                seen.add(test.id)
                hits.append(RetrievalHit.from_entity(test, 1.0, ["graph"], "Covers the target via TESTS edge"))
        # heuristic: tests that call the target, or whose name references it
        name = simple_name(target.qualified_name).lower()
        for rel in self.graph.in_relationships(target.id, [RelationType.CALLS]):
            caller = self.graph.get_entity(rel.src_id)
            if caller and caller.kind == EntityType.TEST and caller.id not in seen:
                seen.add(caller.id)
                hits.append(RetrievalHit.from_entity(caller, 0.9, ["graph"], "Test calls the target"))
        if name:
            for test in self.graph.entities(kind=EntityType.TEST, repo_id=self.repo_id):
                if test.id in seen:
                    continue
                if name in test.name.lower():
                    seen.add(test.id)
                    hits.append(RetrievalHit.from_entity(test, 0.6, ["keyword"], "Test name references the target"))
        return hits

    def _config_for(self, target: Entity) -> list[RetrievalHit]:
        # explicit CONFIGURES edges first
        hits: list[RetrievalHit] = []
        seen: set[str] = set()
        for rel in self.graph.in_relationships(target.id, [RelationType.CONFIGURES, RelationType.CONTROLS]):
            key = self.graph.get_entity(rel.src_id)
            if key and key.id not in seen:
                seen.add(key.id)
                hits.append(RetrievalHit.from_entity(key, 1.0, ["graph"], f"{rel.type.value} the target"))
        # heuristic: config keys whose name appears in the target's source tokens
        tokens = self._target_tokens(target)
        if tokens:
            for key in self.graph.entities(kind=EntityType.CONFIG_KEY, repo_id=self.repo_id):
                if key.id in seen:
                    continue
                if key.name.lower() in tokens:
                    seen.add(key.id)
                    hits.append(RetrievalHit.from_entity(
                        key, 0.5, ["keyword"], "Config key referenced in the target's code"))
        return hits

    def _overrides(self, target: Entity) -> list[RetrievalHit]:
        """Polymorphic risk: same-named methods on sibling/sub classes of the target's class
        (retrieval-strategy §4 'overrides'). A change here may need parallel changes there."""
        if target.kind != EntityType.METHOD:
            return []
        cls_qname = target.qualified_name.rsplit(".", 1)[0]
        method = simple_name(target.qualified_name)
        classes = [e for e in self.graph.find_by_name(simple_name(cls_qname))
                   if e.qualified_name == cls_qname and e.kind == EntityType.CLASS]
        if not classes:
            return []
        cls = classes[0]
        related: dict[str, Entity] = {}
        for rel in self.graph.out_relationships(cls.id, [RelationType.EXTENDS, RelationType.IMPLEMENTS]):
            base = self.graph.get_entity(rel.dst_id)
            if base is None:
                continue
            for sib_rel in self.graph.in_relationships(base.id, [RelationType.EXTENDS, RelationType.IMPLEMENTS]):
                sib = self.graph.get_entity(sib_rel.src_id)
                if sib and sib.id != cls.id:
                    related[sib.qualified_name] = sib
            related.setdefault(base.qualified_name, base)
        for sub_rel in self.graph.in_relationships(cls.id, [RelationType.EXTENDS, RelationType.IMPLEMENTS]):
            sub = self.graph.get_entity(sub_rel.src_id)
            if sub:
                related.setdefault(sub.qualified_name, sub)
        hits: list[RetrievalHit] = []
        for other_qname in sorted(related):
            for cand in self.graph.find_by_name(method):
                if cand.qualified_name == f"{other_qname}.{method}" and cand.kind == EntityType.METHOD:
                    hits.append(RetrievalHit.from_entity(
                        cand, 0.8, ["graph"],
                        f"Same-named method on related class {other_qname} (polymorphic risk)"))
        return hits

    def _data_for(self, target: Entity) -> list[RetrievalHit]:
        hits: list[RetrievalHit] = []
        for rel in self.graph.out_relationships(
            target.id, [RelationType.READS, RelationType.WRITES, RelationType.MAPS_TO]
        ):
            other = self.graph.get_entity(rel.dst_id)
            if other:
                hits.append(RetrievalHit.from_entity(other, 1.0, ["graph"], f"{rel.type.value} by the target"))
        return hits

    def _target_tokens(self, target: Entity) -> set[str]:
        tokens: set[str] = set()
        for chunk in self.metadata.iter_chunks(self.repo_id):
            if chunk.get("entity_id") == target.id or (
                chunk.get("path") == target.provenance.path
                and target.kind in (EntityType.MODULE, EntityType.FILE)
            ):
                tokens |= set(chunk.get("tokens", []))
        return tokens

    def _risk(self, callers, callees, tests, churn) -> dict:
        n_callers = len(callers)
        has_tests = len(tests) > 0
        commits = churn.get("commits", 0)
        fanout = len(callees)
        score = min(0.5, n_callers * 0.06) + min(0.25, commits * 0.03) + min(0.1, fanout * 0.01)
        factors = [f"{n_callers} caller(s)", f"{fanout} callee(s)"]
        if not has_tests:
            score += 0.25
            factors.append("no direct tests")
        else:
            factors.append(f"{len(tests)} covering test(s)")
        if commits:
            factors.append(f"{commits} recent commit(s)")
        level = "high" if score >= 0.66 else "medium" if score >= 0.33 else "low"
        return {"score": round(min(score, 1.0), 2), "level": level, "factors": factors}

    # -- edit context -------------------------------------------------------
    def edit_context(self, query: str) -> dict:
        target = self.resolve_target(query)
        if target is None:
            return {"ok": False, "error": {"code": "not_found", "message": f"symbol not found: {query}"}}
        callers = self._callers(target)
        callees = self._callees(target)
        tests = self._tests(target)
        imports = self._imports_for(target)
        checklist = self._checklist(callers, callees, tests)
        return {
            "ok": True,
            "target": {**_target_dict(target), "source": self._source_slice(target)},
            "callers": [self._with_source(h) for h in callers],
            "callees": [self._with_source(h) for h in callees],
            "tests": [h.to_dict() for h in tests],
            "imports": imports,
            "checklist": checklist,
        }

    def _imports_for(self, target: Entity) -> list[str]:
        module_path = target.provenance.path
        modules = [
            e for e in self.graph.entities(kind=EntityType.MODULE, repo_id=self.repo_id)
            if e.provenance.path == module_path
        ]
        names: list[str] = []
        for module in modules:
            for rel in self.graph.out_relationships(module.id, [RelationType.IMPORTS]):
                dst = self.graph.get_entity(rel.dst_id)
                if dst:
                    names.append(dst.qualified_name)
        return sorted(set(names))

    def _checklist(self, callers, callees, tests) -> list[str]:
        items: list[str] = []
        if callers:
            paths = sorted({h.path for h in callers})
            items.append(f"Update/verify {len(callers)} caller(s) in {', '.join(paths[:5])}")
        if tests:
            tpaths = sorted({h.path for h in tests})
            items.append(f"Re-run {len(tests)} covering test(s) in {', '.join(tpaths[:5])}")
        else:
            items.append("No covering tests found — add a test before changing this symbol")
        if callees:
            items.append(f"Preserve contracts of {len(callees)} callee(s) this symbol depends on")
        return items

    def _with_source(self, hit: RetrievalHit) -> dict:
        data = hit.to_dict()
        entity = self.graph.get_entity(hit.entity_id)
        if entity:
            data["source"] = self._source_slice(entity)
        return data

    def _source_slice(self, entity: Entity) -> str:
        path = entity.provenance.path
        if not path or not entity.provenance.start_line:
            return ""
        full = Path(self.config.repo_path) / path
        try:
            lines = full.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            return ""
        start = max(1, entity.provenance.start_line)
        end = min(len(lines), entity.provenance.end_line or start)
        return "\n".join(lines[start - 1:end])


def _stratify(hits: list[RetrievalHit]) -> tuple[list[RetrievalHit], list[RetrievalHit]]:
    """Split call hits into resolved (R0-R3) and speculative candidate (R4-R5) strata."""
    resolved = [h for h in hits if h.resolution in RESOLVED_LEVELS]
    candidates = [h for h in hits if h.resolution not in RESOLVED_LEVELS]
    return resolved, candidates


def _target_dict(entity: Entity) -> dict:
    return {
        "entity_id": entity.id,
        "kind": entity.kind.value,
        "name": entity.name,
        "qualified_name": entity.qualified_name,
        "path": entity.provenance.path,
        "start_line": entity.provenance.start_line,
        "end_line": entity.provenance.end_line,
    }


__all__ = ["ImpactAnalyzer"]
