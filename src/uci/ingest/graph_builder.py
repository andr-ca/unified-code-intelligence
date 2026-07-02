"""Graph builder / normalizer.

Turns per-file :class:`ParseResult` records into canonical entities and relationships, then resolves
cross-file calls, imports, and inheritance against a global symbol index. This is where UCI's
"structure first" happens — the output is the non-semantic backbone of the knowledge graph.
"""

from __future__ import annotations

import builtins
import sys
from collections import defaultdict
from dataclasses import dataclass

from ..core.entities import SYMBOL_KINDS, Entity, EntityType
from ..core.ids import entity_id, relationship_id
from ..core.provenance import Provenance
from ..core.relationships import RESOLVED_LEVELS, Relationship, RelationType
from ..parser.base import ParseResult

_DEFINER_KINDS = frozenset(
    {EntityType.MODULE, EntityType.CLASS, EntityType.PACKAGE, EntityType.FILE}
)
_CALLABLE = frozenset({EntityType.FUNCTION, EntityType.METHOD, EntityType.TEST,
                       EntityType.LEGACY_PROGRAM, EntityType.PARAGRAPH})
_CLASSISH = frozenset({EntityType.CLASS, EntityType.INTERFACE})

#: Languages using the flat member-library convention (name == origin; a literal reference
#: that doesn't resolve is a missing artifact, never a global name-match fallback).
_MAINFRAME_LANGS = frozenset({"cobol", "jcl", "csd", "hlasm"})

#: z/OS system utilities commonly executed from JCL — external by convention, never gaps.
_SYSTEM_UTILITIES = frozenset({
    "IDCAMS", "IEBGENER", "IEBCOPY", "IEFBR14", "SORT", "ICETOOL", "ICEMAN",
    "IKJEFT01", "IKJEFT1A", "IKJEFT1B", "SDSF", "FTP", "ADRDSSU", "GIMSMP",
})

_LINK_RELATIONS = {
    "runs": RelationType.RUNS,
    "invokes": RelationType.INVOKES,
    "reads": RelationType.READS,
    "writes": RelationType.WRITES,
    "maps_to": RelationType.MAPS_TO,        # DCLGEN copybook -> table
    "depends_on": RelationType.DEPENDS_ON,  # HLASM EXTRN/WXTRN -> external symbol
    "uses": RelationType.USES,              # program -> BMS screen (SEND/RECEIVE MAP)
    "performs": RelationType.CALLS,         # paragraph -> paragraph (intra-program)
}

#: Link target kinds that are *materialized* (created on first reference) rather than resolved:
#: tables, datasets, and screens are named artifacts, not source members.
_MATERIALIZED_KINDS = frozenset({
    EntityType.DATABASE_TABLE.value, EntityType.DATASET.value, EntityType.SCREEN.value,
})

#: If a bare name matches more than this many symbols, emit no edge (record it as unresolved).
_FANOUT_CAP = 5

#: Reserved path segment for placeholder (stub) entities that stand in for missing artifacts.
MISSING_SEGMENT = "__missing__"

#: Python standard-library module names (used to classify unresolved imports as external, not gaps).
_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))

#: Names that resolve to language builtins/globals — a bare call to these is not a "not-found" gap.
_BUILTINS = frozenset(dir(builtins)) | frozenset({
    "console", "require", "JSON", "Object", "Array", "Math", "Promise", "Set", "Map",
    "setTimeout", "setInterval", "fetch", "parseInt", "parseFloat", "String", "Number",
    "Boolean", "Symbol", "Date", "RegExp", "Error", "document", "window",
})


@dataclass
class FileParse:
    path: str
    language: str
    module_qname: str
    result: ParseResult


def _parent_qname(qname: str) -> str:
    return qname.rsplit(".", 1)[0] if "." in qname else ""


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


class GraphBuilder:
    def __init__(self, repo_id: str, repo_name: str, repo_root: str,
                 external_prefixes: tuple[str, ...] = ()) -> None:
        self.repo_id = repo_id
        self.repo_name = repo_name
        self.repo_root = repo_root
        self.external_prefixes = tuple(p.upper() for p in external_prefixes)
        self.entities: dict[str, Entity] = {}
        self.relationships: list[Relationship] = []
        self._rel_ord: dict[tuple, int] = defaultdict(int)
        # indices
        self.by_qname: dict[str, list[Entity]] = defaultdict(list)
        self.by_name: dict[str, list[Entity]] = defaultdict(list)
        self.modules_by_qname: dict[str, Entity] = {}
        self.module_by_path: dict[str, Entity] = {}
        self._packages: dict[str, Entity] = {}
        #: call sites that could not be attributed to a single callee (for completeness reporting)
        self.unresolved_calls: list[dict] = []
        #: gap registry: (artifact_kind, name) -> gap record for missing artifacts
        self.gaps: dict[tuple[str, str], dict] = {}
        #: shared import tables (module -> imported modules / local-name bindings)
        self._import_map: dict[str, set[str]] = defaultdict(set)
        self._binds_by_module: dict[str, dict[str, str]] = defaultdict(dict)

    # -- helpers ------------------------------------------------------------
    def _prov(self, path: str = "", start: int = 0, end: int = 0, extractor: str = "graph_builder") -> Provenance:
        return Provenance(self.repo_id, path, start, end, extractor, 1.0)

    def _add_entity(self, kind: EntityType, path: str, qname: str, name: str,
                    prov: Provenance, attributes: dict | None = None) -> Entity:
        eid = entity_id(kind, self.repo_id, path, qname)
        entity = self.entities.get(eid)
        if entity is None:
            entity = Entity(id=eid, kind=kind, name=name, qualified_name=qname,
                            provenance=prov, attributes=attributes or {})
            self.entities[eid] = entity
            self.by_qname[qname].append(entity)
            self.by_name[name.lower()].append(entity)
            if kind == EntityType.MODULE:
                self.modules_by_qname[qname] = entity
        return entity

    def _add_rel(self, rtype: RelationType, src: str, dst: str, prov: Provenance,
                 attributes: dict | None = None) -> None:
        if src == dst:
            return
        key = (rtype, src, dst)
        ordinal = self._rel_ord[key]
        self._rel_ord[key] = ordinal + 1
        rid = relationship_id(rtype, src, dst, ordinal if ordinal else None)
        self.relationships.append(
            Relationship(id=rid, type=rtype, src_id=src, dst_id=dst, provenance=prov,
                         attributes=attributes or {})
        )

    # -- build --------------------------------------------------------------
    def build(self, files: list[FileParse]) -> tuple[list[Entity], list[Relationship]]:
        repo = self._add_entity(
            EntityType.REPOSITORY, "", self.repo_name, self.repo_name,
            self._prov(), {"root": self.repo_root},
        )
        self._build_structure(repo, files)
        self._build_symbols(files)
        self._compute_import_tables(files)
        self._resolve_imports(files)
        self._resolve_inheritance(files)
        self._resolve_calls(files)
        self._resolve_links(files)
        return list(self.entities.values()), self.relationships

    def _is_external_name(self, name: str) -> bool:
        """System/vendor artifact by naming convention (DFH*, MQ*, CEE*, utilities, …)."""
        up = name.upper()
        return up in _SYSTEM_UTILITIES or any(up.startswith(p) for p in self.external_prefixes)

    def _compute_import_tables(self, files: list[FileParse]) -> None:
        """Per-module imported-module set + local-name binding table, shared by all resolvers."""
        for fp in files:
            for imp in fp.result.imports:
                if imp.module:
                    self._import_map[fp.module_qname].add(imp.module)
                for local, target in imp.binds.items():
                    self._binds_by_module[fp.module_qname][local] = target

    def _build_structure(self, repo: Entity, files: list[FileParse]) -> None:
        dirs: set[str] = set()
        for fp in files:
            parts = fp.path.split("/")
            for i in range(len(parts) - 1):
                dirs.add("/".join(parts[: i + 1]))
        dir_entities: dict[str, Entity] = {}
        for d in sorted(dirs):
            name = d.rsplit("/", 1)[-1]
            dir_entities[d] = self._add_entity(EntityType.DIRECTORY, d, d, name, self._prov(d))
        for d in sorted(dirs):
            parent = _dirname(d)
            src = dir_entities[parent].id if parent else repo.id
            self._add_rel(RelationType.CONTAINS, src, dir_entities[d].id, self._prov(d))

        for fp in files:
            file_ent = self._add_entity(
                EntityType.FILE, fp.path, fp.path, fp.path.rsplit("/", 1)[-1],
                self._prov(fp.path), {"language": fp.language},
            )
            parent = _dirname(fp.path)
            src = dir_entities[parent].id if parent else repo.id
            self._add_rel(RelationType.CONTAINS, src, file_ent.id, self._prov(fp.path))

            module = self._add_entity(
                EntityType.MODULE, fp.path, fp.module_qname, fp.module_qname.rsplit(".", 1)[-1] or fp.path,
                self._prov(fp.path), {"language": fp.language, "module": fp.module_qname},
            )
            self.module_by_path[fp.path] = module
            self._add_rel(RelationType.CONTAINS, file_ent.id, module.id, self._prov(fp.path))

    def _build_symbols(self, files: list[FileParse]) -> None:
        for fp in files:
            module = self.module_by_path[fp.path]
            for sym in fp.result.symbols:
                attributes = {
                    "language": fp.language, "module": fp.module_qname,
                    "signature": sym.signature, "docstring": sym.docstring[:500],
                    "decorators": sym.decorators, "bases": sym.bases,
                    "is_exported": sym.is_exported, **sym.attributes,
                }
                ent = self._add_entity(
                    sym.kind, fp.path, sym.qualified_name, sym.name,
                    self._prov(fp.path, sym.start_line, sym.end_line, f"{fp.language}_parser"),
                    attributes,
                )
                definer = self._resolve_definer(sym.parent_qname, module)
                rtype = RelationType.DEFINES if sym.kind in SYMBOL_KINDS else RelationType.CONTAINS
                self._add_rel(rtype, definer.id, ent.id,
                              self._prov(fp.path, sym.start_line, sym.end_line))

    def _resolve_definer(self, parent_qname: str | None, module: Entity) -> Entity:
        if parent_qname:
            for cand in self.by_qname.get(parent_qname, []):
                if cand.kind in _DEFINER_KINDS:
                    return cand
        return module

    # -- resolution ---------------------------------------------------------
    def _internal_top_levels(self) -> set[str]:
        return {q.split(".")[0] for q in self.modules_by_qname if q}

    def _ensure_package(self, module: str) -> Entity:
        top = module.split(".")[0] or module
        pkg = self._packages.get(top)
        if pkg is None:
            pkg = self._add_entity(EntityType.PACKAGE, "", top, top, self._prov(),
                                   {"external": True})
            self._packages[top] = pkg
        return pkg

    def _is_external_module(self, module: str, imp_external: bool, tops: set[str]) -> bool:
        """Classify an unresolved import as external (stub, no gap) vs missing (stub + gap)."""
        if imp_external:
            return True
        top = module.split(".")[0]
        if top in _STDLIB:
            return True
        upper = module.upper()
        if any(upper.startswith(prefix) for prefix in self.external_prefixes):
            return True
        # a top-level package that exists in the repo but whose specific module is absent = MISSING
        return top not in tops

    def report_gap(self, artifact_kind: str, name: str, stub_kind: EntityType,
                   site: Provenance, reason: str, expected_origin: str = "",
                   external: bool = False) -> Entity:
        """Never drop a resolution failure: create/refresh a placeholder (stub) entity and, unless the
        target is external, a gap record naming the missing artifact and this referencing site."""
        stub_id = entity_id(stub_kind, self.repo_id, MISSING_SEGMENT, name)
        stub = self.entities.get(stub_id)
        if stub is None:
            stub = Entity(
                id=stub_id, kind=stub_kind, name=name.split(".")[-1], qualified_name=name,
                provenance=Provenance(self.repo_id, "", 0, 0, "normalizer", 0.0),
                attributes={"missing": not external, "external": external,
                            "expected_origin": expected_origin},
            )
            self.entities[stub_id] = stub
            self.by_qname[name].append(stub)
            self.by_name[name.lower()].append(stub)
        if not external:
            key = (artifact_kind, name)
            gap = self.gaps.get(key)
            if gap is None:
                gap = {
                    "artifact_kind": artifact_kind, "name": name, "stub_entity_id": stub_id,
                    "expected_origin": expected_origin, "reasons": set(),
                    "referencing_sites": [], "ref_count": 0, "confidence": 0.8,
                }
                self.gaps[key] = gap
            gap["reasons"].add(reason)
            gap["referencing_sites"].append({"path": site.path, "line": site.start_line})
            gap["ref_count"] += 1
        return stub

    def gap_records(self, generation: int = 0, first_seen: str = "") -> list[dict]:
        """Materialize gap records (reasons as sorted list) for persistence, ranked by fan-in."""
        out = []
        for gap in self.gaps.values():
            out.append({
                **gap,
                "reasons": sorted(gap["reasons"]),
                "last_seen_generation": generation,
                "first_seen": first_seen,
            })
        out.sort(key=lambda g: g["ref_count"], reverse=True)
        return out

    def _resolve_imports(self, files: list[FileParse]) -> None:
        repo_id_ent = entity_id(EntityType.REPOSITORY, self.repo_id, "", self.repo_name)
        tops = self._internal_top_levels()
        for fp in files:
            src = self.module_by_path[fp.path]
            for imp in fp.result.imports:
                target = self.modules_by_qname.get(imp.module)
                if fp.language in _MAINFRAME_LANGS:
                    # COPY MEMBER: land the edge on the COPYBOOK/program symbol so impact
                    # queries on the copybook see its dependents directly
                    member = self._resolve_member(
                        imp.module, kinds=(EntityType.COPYBOOK, EntityType.LEGACY_PROGRAM)
                    )
                    if member is not None:
                        target = member
                prov = self._prov(fp.path, imp.start_line, imp.start_line)
                if target is not None:
                    self._add_rel(RelationType.IMPORTS, src.id, target.id, prov,
                                  {"names": imp.names})
                    continue
                if not imp.module:
                    continue
                mainframe = fp.language in _MAINFRAME_LANGS
                if mainframe:
                    # flat member namespace: a COPY that doesn't resolve is either a platform
                    # copybook (DFH*/SQLCA -> external stub) or a missing member (gap)
                    if imp.external or self._is_external_name(imp.module):
                        stub = self.report_gap("copybook", imp.module, EntityType.COPYBOOK, prov,
                                               "external-copybook", "", external=True)
                        self._add_rel(RelationType.IMPORTS, src.id, stub.id, prov,
                                      {"names": imp.names, "resolution": "external"})
                    else:
                        stub = self.report_gap("copybook", imp.module, EntityType.COPYBOOK, prov,
                                               "copy-member-not-found",
                                               f"{imp.module}.cpy (copybook library)", external=False)
                        self._add_rel(RelationType.IMPORTS, src.id, stub.id, prov,
                                      {"names": imp.names, "resolution": "missing"})
                elif self._is_external_module(imp.module, imp.external, tops):
                    pkg = self._ensure_package(imp.module)
                    self._add_rel(RelationType.IMPORTS, src.id, pkg.id, prov, {"names": imp.names})
                    self._add_rel(RelationType.DEPENDS_ON, repo_id_ent, pkg.id, prov)
                else:
                    # internal module referenced but not indexed -> gap + missing module stub
                    expected = imp.module.replace(".", "/") + " (expected module file)"
                    stub = self.report_gap("module", imp.module, EntityType.MODULE, prov,
                                           "import-unresolved", expected, external=False)
                    self._add_rel(RelationType.IMPORTS, src.id, stub.id, prov,
                                  {"names": imp.names, "resolution": "missing"})

    def _by_qname_class(self, qname: str) -> Entity | None:
        for entity in self.by_qname.get(qname, []):
            if entity.kind in _CLASSISH:
                return entity
        return None

    def _resolve_type_ref(
        self, name: str, src_module: str, binds: dict[str, str], imported: set[str]
    ) -> tuple[Entity | None, str, float]:
        """Resolve a class/interface reference (base class, instantiation) with the same ladder as
        calls, so inheritance edges are labeled and don't over-claim on ambiguous bare names."""
        # binds first (highest rung): handles aliased bases `from x import Thing as T; class C(T)`
        if name in binds:
            target = self._by_qname_class(binds[name])
            if target:
                return target, "import-traced", 0.95
            # binds names an unindexed origin -> gap via _maybe_ref_gap, never a weaker global fallback
            # that would contradict the import statement (recommendations §13.2)
            return None, "", 0.0
        cands = [e for e in self.by_name.get(name.lower(), []) if e.kind in _CLASSISH]
        if not cands:
            return None, "", 0.0
        same = [c for c in cands if c.attributes.get("module") == src_module]
        if len(same) == 1:
            return same[0], "syntactic", 1.0
        imp = [c for c in cands if c.attributes.get("module") in imported]
        if len(imp) == 1:
            return imp[0], "import-traced", 0.95
        if len(cands) == 1:
            return cands[0], "name-match", 0.6
        return cands[0], "candidate", min(0.4, 1.0 / len(cands))

    def _resolve_inheritance(self, files: list[FileParse]) -> None:
        tops = self._internal_top_levels()
        for fp in files:
            binds = self._binds_by_module.get(fp.module_qname, {})
            imported = self._import_map.get(fp.module_qname, set())
            for ref in fp.result.references:
                sources = list(self.by_qname.get(ref.from_qname, []))
                if not sources:
                    continue
                src = sources[0]
                src_module = src.attributes.get("module", fp.module_qname)
                target, resolution, confidence = self._resolve_type_ref(
                    ref.name, src_module, binds, imported
                )
                if target is None:
                    self._maybe_ref_gap(ref, src, fp, binds, tops)
                    continue
                prov = Provenance(self.repo_id, fp.path, ref.start_line, ref.start_line,
                                  f"{fp.language}_parser", confidence)
                if ref.kind == "base":
                    rtype = RelationType.IMPLEMENTS if target.kind == EntityType.INTERFACE else RelationType.EXTENDS
                    self._add_rel(rtype, src.id, target.id, prov, {"resolution": resolution})
                else:  # instantiation / reference
                    self._add_rel(RelationType.REFERENCES, src.id, target.id, prov,
                                  {"kind": ref.kind, "resolution": resolution})

    def _maybe_ref_gap(self, ref, src: Entity, fp: FileParse, binds: dict[str, str], tops: set[str]) -> None:
        """Never drop an unresolved type reference (recommendations §12.2). When the base/instantiated
        name traces (via imports) to an internal module that isn't indexed, record a class gap + stub
        edge; external bases (e.g. pydantic.BaseModel) become external stubs with no gap."""
        target_qn = binds.get(ref.name)
        if not target_qn:
            return  # can't name the artifact (dynamic/unknown base) — skip to avoid noise
        module = target_qn.rsplit(".", 1)[0] if "." in target_qn else target_qn
        prov = Provenance(self.repo_id, fp.path, ref.start_line, ref.start_line, "normalizer", 0.0)
        external = self._is_external_module(module, False, tops)
        reason = "external-base" if external else "base-class-not-indexed"
        stub = self.report_gap("class", target_qn, EntityType.CLASS, prov, reason,
                               target_qn.replace(".", "/"), external=external)
        rtype = RelationType.EXTENDS if ref.kind == "base" else RelationType.REFERENCES
        # external bases (e.g. pydantic.BaseModel) are expected-absent, not "obtain me" (§13.3)
        attrs = {"resolution": "external" if external else "missing"}
        if ref.kind != "base":
            attrs["kind"] = ref.kind
        self._add_rel(rtype, src.id, stub.id, prov, attrs)

    def _resolve_calls(self, files: list[FileParse]) -> None:
        ancestors = self._ancestor_map()
        tops = self._internal_top_levels()

        for fp in files:
            imported = self._import_map.get(fp.module_qname, set())
            binds = self._binds_by_module.get(fp.module_qname, {})
            for call in fp.result.calls:
                callers = self.by_qname.get(call.caller_qname, [])
                if not callers:
                    continue
                # prefer the callable symbol over a same-named module/file (mainframe: the
                # LEGACY_PROGRAM and its MODULE share one qualified name)
                caller = next((c for c in callers if c.kind in _CALLABLE), callers[0])
                # dynamic targets (COBOL CALL WS-PGM, CICS XCTL(var), JCL PGM=&VAR) are never
                # resolvable from source — record the site, never guess an edge
                if call.dynamic:
                    self.unresolved_calls.append({
                        "caller": caller.qualified_name, "name": call.callee_name,
                        "receiver": call.receiver, "reason": "dynamic-target",
                        "path": fp.path, "line": call.start_line, "fan_out": 0,
                    })
                    continue
                if fp.language in _MAINFRAME_LANGS:
                    # a literal CALL/XCTL/LINK names its member exactly (flat namespace):
                    # unique member -> provable edge; external API -> external stub; else gap.
                    # Dataflow-recovered targets (MOVE 'X' TO var) are R2 "inferred", never R0.
                    resolution = "inferred" if call.via_dataflow else "syntactic"
                    confidence = 0.9 if call.via_dataflow else 1.0
                    target = self._resolve_member(call.callee_name)
                    if target is not None:
                        prov = Provenance(self.repo_id, fp.path, call.start_line, call.start_line,
                                          f"{fp.language}_parser", confidence)
                        self._add_rel(RelationType.CALLS, caller.id, target.id, prov,
                                      {"callee": call.callee_name, "resolution": resolution,
                                       "fan_out": 1})
                    else:
                        self._emit_mainframe_call(caller, call, fp)
                    continue
                # binds-first: an explicit import is the strongest evidence and overrides the global
                # ladder — a binds miss becomes a gap, never a same-named unrelated symbol (§13.2)
                bound = self._resolve_via_binds(call, binds)
                if bound is not None:
                    self._emit_bound_call(caller, call, bound, fp, tops)
                    continue
                target, confidence, resolution, fan_out = self._resolve_callee(
                    call, caller, imported, ancestors
                )
                if target is None:
                    reason = self._unresolved_reason(call, fan_out, binds)
                    if reason:
                        self.unresolved_calls.append({
                            "caller": caller.qualified_name, "name": call.callee_name,
                            "receiver": call.receiver, "reason": reason,
                            "path": fp.path, "line": call.start_line, "fan_out": fan_out,
                        })
                    continue
                prov = Provenance(self.repo_id, fp.path, call.start_line, call.start_line,
                                  f"{fp.language}_parser", confidence)
                self._add_rel(RelationType.CALLS, caller.id, target.id, prov,
                              {"receiver": call.receiver, "callee": call.callee_name,
                               "resolution": resolution, "fan_out": fan_out})

    def _resolve_via_binds(self, call, binds: dict[str, str]):
        """Resolve a call through the import binding table. Returns one of:
        ``("resolved", entity)`` · ``("skip", None)`` (constructor — the instantiation ref handles it) ·
        ``("missing", target_qname)`` (binds names an unindexed origin) · ``None`` (not a binds hit).
        """
        callee = call.callee_name
        recv = call.receiver
        if callee in binds:
            bt = binds[callee]
            target = self._by_qname_callable(bt) or self._by_qname_callable(f"{bt}.{callee}")
            if target:
                return ("resolved", target)
            if callee[:1].isupper() or self._by_qname_class(bt):
                return ("skip", None)  # constructor of a class — REFERENCES/instantiation path owns it
            return ("missing", bt)
        if recv and recv in binds:
            base = binds[recv]
            target = self._by_qname_callable(f"{base}.{callee}")
            if target:
                return ("resolved", target)
            if callee[:1].isupper():
                return ("skip", None)
            return ("missing", f"{base}.{callee}")
        return None

    def _emit_bound_call(self, caller: Entity, call, bound, fp: FileParse, tops: set[str]) -> None:
        kind, value = bound
        if kind == "skip":
            return
        if kind == "resolved":
            prov = Provenance(self.repo_id, fp.path, call.start_line, call.start_line,
                              f"{fp.language}_parser", 0.95)
            self._add_rel(RelationType.CALLS, caller.id, value.id, prov,
                          {"receiver": call.receiver, "callee": call.callee_name,
                           "resolution": "import-traced", "fan_out": 1})
            return
        # "missing": value is the bound target qname naming an unindexed origin -> gap + stub edge
        module = value.rsplit(".", 1)[0] if "." in value else value
        external = self._is_external_module(module, False, tops)
        prov = Provenance(self.repo_id, fp.path, call.start_line, call.start_line, "normalizer", 0.0)
        reason = "external-call" if external else "call-target-not-indexed"
        stub = self.report_gap("function", value, EntityType.FUNCTION, prov, reason,
                               value.replace(".", "/") if not external else "", external=external)
        self._add_rel(RelationType.CALLS, caller.id, stub.id, prov,
                      {"receiver": call.receiver, "callee": call.callee_name,
                       "resolution": "external" if external else "missing", "fan_out": -1})

    def _emit_mainframe_call(self, caller: Entity, call, fp: FileParse) -> None:
        """A literal mainframe CALL/XCTL/LINK to an unindexed member: external API or program gap."""
        name = call.callee_name.upper()
        external = self._is_external_name(name)
        prov = Provenance(self.repo_id, fp.path, call.start_line, call.start_line, "normalizer", 0.0)
        reason = "external-call" if external else "call-target-not-indexed"
        stub = self.report_gap("program", name, EntityType.LEGACY_PROGRAM, prov, reason,
                               "" if external else f"{name} (load library / source member)",
                               external=external)
        self._add_rel(RelationType.CALLS, caller.id, stub.id, prov,
                      {"callee": name, "resolution": "external" if external else "missing",
                       "fan_out": -1})

    #: Which entity kind a link's source must be — member names collide across artifact types
    #: (CBEXPORT is both a program and the job that runs it), so "runs" must anchor on the job.
    _LINK_SRC_KINDS = {
        "runs": (EntityType.JCL_JOB,),
        "invokes": (EntityType.TRANSACTION_CODE,),
        "reads": (EntityType.LEGACY_PROGRAM, EntityType.JCL_JOB, EntityType.FUNCTION, EntityType.METHOD),
        "writes": (EntityType.LEGACY_PROGRAM, EntityType.JCL_JOB, EntityType.FUNCTION, EntityType.METHOD),
        "maps_to": (EntityType.COPYBOOK,),
        "depends_on": (EntityType.LEGACY_PROGRAM,),
        "uses": (EntityType.LEGACY_PROGRAM,),
        "performs": (EntityType.PARAGRAPH, EntityType.LEGACY_PROGRAM),
    }

    def _resolve_links(self, files: list[FileParse]) -> None:
        """Materialize parser-emitted structural links: JCL RUNS, CSD INVOKES, SQL READS/WRITES."""
        for fp in files:
            for link in fp.result.links:
                rtype = _LINK_RELATIONS.get(link.relation)
                sources = self.by_qname.get(link.src_qname, [])
                preferred = self._LINK_SRC_KINDS.get(link.relation, ())
                # same member name can exist as .jcl AND .prc (CardDemo TRANREPT): anchor the
                # link on the symbol from *this* file, falling back to kind preference
                src = (next((s for s in sources
                             if s.kind in preferred and s.provenance.path == fp.path), None)
                       or next((s for s in sources if s.kind in preferred), None)
                       or next((s for s in sources if s.kind in SYMBOL_KINDS), None)
                       or (sources[0] if sources else None))
                if rtype is None or src is None:
                    continue
                prov = Provenance(self.repo_id, fp.path, link.start_line, link.start_line,
                                  f"{fp.language}_parser", 1.0)
                if link.target_kind in _MATERIALIZED_KINDS:
                    target = self._ensure_named(EntityType(link.target_kind), link.target_name, prov)
                    self._add_rel(rtype, src.id, target.id, prov,
                                  {"resolution": "syntactic", **link.attributes})
                    continue
                if link.relation == "performs":
                    # intra-program paragraph reference: PROGRAM.PARA (no gap when absent —
                    # sections/exits are commonly perform targets without their own label)
                    program = link.attributes.get("program", "")
                    para = self._by_qname_kind(f"{program}.{link.target_name}", EntityType.PARAGRAPH)
                    if para is not None:
                        self._add_rel(rtype, src.id, para.id, prov, {"resolution": "syntactic"})
                    continue
                target = self._resolve_member(link.target_name)
                if target is not None:
                    self._add_rel(rtype, src.id, target.id, prov,
                                  {"resolution": "syntactic", **link.attributes})
                elif self._is_external_name(link.target_name):
                    stub = self.report_gap("program", link.target_name.upper(),
                                           EntityType.LEGACY_PROGRAM, prov, "external-program",
                                           "", external=True)
                    self._add_rel(rtype, src.id, stub.id, prov,
                                  {"resolution": "external", **link.attributes})
                else:
                    kind = "proc" if link.target_kind == EntityType.JCL_JOB.value else "program"
                    stub = self.report_gap(kind, link.target_name.upper(),
                                           EntityType(link.target_kind), prov,
                                           f"{kind}-not-indexed",
                                           f"{link.target_name} (library member)", external=False)
                    self._add_rel(rtype, src.id, stub.id, prov,
                                  {"resolution": "missing", **link.attributes})

    def _resolve_member(
        self, name: str,
        kinds: tuple[EntityType, ...] = (EntityType.LEGACY_PROGRAM, EntityType.JCL_JOB),
    ) -> Entity | None:
        """Resolve a mainframe member name to its symbol entity (flat namespace), falling back
        to the member's MODULE entity when no symbol of the preferred kinds exists.

        ``kinds`` is a **priority order**, not a set: member names collide across artifact
        types (CREACC is a program *and* its COMMAREA copybook; CBEXPORT is a program *and*
        the job that runs it), so COPY must prefer the copybook while CALL/PGM= must prefer
        the program.
        """
        candidates = self.by_name.get(name.lower(), [])
        for kind in kinds:
            for entity in candidates:
                if entity.kind == kind and not entity.attributes.get("missing"):
                    return entity
        for entity in candidates:
            if entity.kind == EntityType.MODULE and not entity.attributes.get("missing"):
                return entity
        return None

    def _ensure_named(self, kind: EntityType, name: str, prov: Provenance) -> Entity:
        """Resolve a named artifact (table / dataset / screen) to an already-parsed entity of the
        same kind (e.g. a BMS-defined SCREEN, a CSD-defined DATASET), or materialize it on first
        reference."""
        qname = name.upper()
        existing = self._by_qname_kind(qname, kind) or (
            next((e for e in self.by_name.get(qname.lower(), []) if e.kind == kind), None)
        )
        if existing is not None:
            return existing
        return self._add_entity(kind, "", qname, qname.split(".")[-1], prov, {kind.value: True})

    def _by_qname_kind(self, qname: str, kind: EntityType) -> Entity | None:
        for entity in self.by_qname.get(qname, []):
            if entity.kind == kind:
                return entity
        return None

    def _unresolved_reason(self, call, fan_out: int, binds: dict[str, str]) -> str | None:
        """Classify an unattributed call so completeness never silently reports 'exact' for a symbol
        whose callers are hidden behind dynamic dispatch (recommendations §1.6 / §11.2)."""
        if fan_out > _FANOUT_CAP:
            return "fan-out-capped"
        if fan_out != 0:
            return None
        receiver = call.receiver
        if receiver in ("self", "this", "cls"):
            return None  # method on own class not found (inherited/typo) — not a hidden caller
        if receiver and receiver in binds:
            return None  # receiver is an imported module/alias (external call, expected)
        if call.callee_name in binds or call.callee_name in _BUILTINS:
            return None  # imported name or language builtin
        if not receiver and call.callee_name[:1].isupper():
            # A bare Capitalized callee is a constructor/instantiation, not a hidden dynamic call.
            # The parser emits the same site as an `instantiation` reference (python_parser.py), which
            # `_resolve_inheritance` turns into a REFERENCES edge (resolved) or a class gap (§10). Recording
            # it *again* here as a not-found call would double-count and over-hedge the caller's
            # completeness (evals: same-module `DiscountRule()` must not make `calculate` non-exact).
            return None
        if receiver:
            return "dynamic-receiver"  # method call on an untyped local/param
        return "not-found"  # bare function neither indexed nor a builtin

    def _ancestor_map(self) -> dict[str, set[str]]:
        """Transitive class-ancestor map from *resolved* EXTENDS/IMPLEMENTS edges (built before call
        resolution). Speculative (name-match/candidate) inheritance edges are excluded so a wrong
        base-class guess cannot corrupt downstream R3 call resolution (recommendations §11.2)."""
        direct: dict[str, set[str]] = defaultdict(set)
        for rel in self.relationships:
            if rel.type in (RelationType.EXTENDS, RelationType.IMPLEMENTS) and \
                    rel.attributes.get("resolution") in RESOLVED_LEVELS:
                src = self.entities.get(rel.src_id)
                dst = self.entities.get(rel.dst_id)
                if src and dst:
                    direct[src.qualified_name].add(dst.qualified_name)
        closure: dict[str, set[str]] = {}
        for cls in direct:
            seen: set[str] = set()
            stack = list(direct[cls])
            while stack:
                anc = stack.pop()
                if anc in seen:
                    continue
                seen.add(anc)
                stack.extend(direct.get(anc, ()))
            closure[cls] = seen
        return closure

    def _by_qname_callable(self, qname: str) -> Entity | None:
        for entity in self.by_qname.get(qname, []):
            if entity.kind in _CALLABLE:
                return entity
        return None

    def _method_in_class(self, class_qname: str, callee: str, ancestors: dict[str, set[str]]) -> Entity | None:
        found = self._by_qname_callable(f"{class_qname}.{callee}")
        if found:
            return found
        for anc in ancestors.get(class_qname, ()):
            found = self._by_qname_callable(f"{anc}.{callee}")
            if found:
                return found
        return None

    def _narrowed_class(self, name: str, caller_module: str, imported: set[str]) -> Entity | None:
        """Resolve a receiver's class name, narrowing by the caller's module/imports. Returns None
        when the name is ambiguous, so R2 'inferred' never over-claims on a global first-match."""
        cands = [e for e in self.by_name.get(name.lower(), []) if e.kind in _CLASSISH]
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        same = [c for c in cands if c.attributes.get("module") == caller_module]
        if len(same) == 1:
            return same[0]
        imp = [c for c in cands if c.attributes.get("module") in imported]
        if len(imp) == 1:
            return imp[0]
        return None

    def _resolve_callee(
        self, call, caller: Entity, imported: set[str], ancestors: dict[str, set[str]],
    ) -> tuple[Entity | None, float, str, int]:
        """Resolve a call via the global ladder. Import-binding evidence is handled earlier by
        :meth:`_resolve_via_binds`, so this only runs for calls with no explicit import binding.

        R2 inferred (receiver type) → R0 syntactic (self/same-module) → R1 import-traced (imported
        module) → R3 inherited → R4 name-match → R5 candidate (fan-out-capped). Confidence is derived.
        """
        callee = call.callee_name
        caller_class = _parent_qname(caller.qualified_name)
        caller_module = caller.attributes.get("module", "")

        # R2 inferred: receiver has an inferred class (local `x = Cls()`, annotation, or `Cls.m()`)
        rtype = call.receiver_type or (call.receiver if call.receiver and call.receiver[:1].isupper() else None)
        if rtype:
            cls = self._narrowed_class(rtype, caller_module, imported)
            if cls:
                method = self._method_in_class(cls.qualified_name, callee, ancestors)
                if method:
                    return method, 0.9, "inferred", 1

        # R0 syntactic: self/this/cls resolved within the defining class hierarchy
        if call.receiver in ("self", "this", "cls") and caller_class:
            method = self._method_in_class(caller_class, callee, ancestors)
            if method:
                return method, 1.0, "syntactic", 1

        candidates = [e for e in self.by_name.get(callee.lower(), []) if e.kind in _CALLABLE]
        if not candidates:
            return None, 0.0, "", 0

        # R0 syntactic: unique same-module definition
        same_module = [c for c in candidates if c.attributes.get("module") == caller_module]
        if len(same_module) == 1:
            return same_module[0], 1.0, "syntactic", 1
        # R1 import-traced: single candidate in a module the caller imports
        imported_cands = [c for c in candidates if c.attributes.get("module") in imported]
        if len(imported_cands) == 1:
            return imported_cands[0], 0.95, "import-traced", 1
        # R3 inherited: candidate on an ancestor class of the caller's class
        if caller_class:
            anc = ancestors.get(caller_class, set())
            inherited = [c for c in candidates if _parent_qname(c.qualified_name) in anc]
            if inherited:
                return inherited[0], 0.9, "inherited", 1
        # R4 name-match: globally unique name
        if len(candidates) == 1:
            return candidates[0], 0.6, "name-match", 1
        # ambiguous even after import narrowing -> speculative candidate, NOT resolved (§11.1)
        if imported_cands:
            return imported_cands[0], min(0.4, 1.0 / len(imported_cands)), "candidate", len(imported_cands)
        # R5 candidate: ambiguous. Drop noisy common names above the fan-out cap (recorded as unresolved).
        fan_out = len(candidates)
        if fan_out > _FANOUT_CAP:
            return None, 0.0, "", fan_out
        return candidates[0], min(0.4, 1.0 / fan_out), "candidate", fan_out


__all__ = ["GraphBuilder", "FileParse", "RESOLVED_LEVELS"]
