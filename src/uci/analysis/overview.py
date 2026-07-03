"""Repo overview and module explanation — human- and agent-facing summaries derived purely from the
canonical graph (no LLM required)."""

from __future__ import annotations

from collections import Counter, defaultdict

from ..core.entities import SYMBOL_KINDS, Entity, EntityType
from ..core.interfaces import GraphStore, MetadataStore
from ..core.relationships import RelationType


def _rank_key_symbols(graph: GraphStore, paths_by_id: dict[str, str]) -> list[dict]:
    """Rank hubs by how many *other files* depend on them (a true system hub), not by raw
    call volume, which in COBOL is dominated by intra-program paragraphs PERFORMed many
    times within one file. Fall back to raw fan-in only when nothing crosses a file
    boundary (tiny or single-file repos)."""
    total_fan_in: Counter[str] = Counter()
    cross_file_callers: defaultdict[str, set[str]] = defaultdict(set)
    for rel in graph.relationships(RelationType.CALLS):
        total_fan_in[rel.dst_id] += 1
        src_path, dst_path = paths_by_id.get(rel.src_id), paths_by_id.get(rel.dst_id)
        if src_path and dst_path and src_path != dst_path:
            cross_file_callers[rel.dst_id].add(src_path)

    ranked: list[tuple[str, int]] = sorted(
        ((eid, len(files)) for eid, files in cross_file_callers.items()),
        key=lambda kv: kv[1], reverse=True,
    ) or total_fan_in.most_common(15)

    key_symbols = []
    for entity_id, count in ranked[:15]:
        entity = graph.get_entity(entity_id)
        if entity and entity.kind in SYMBOL_KINDS:
            key_symbols.append({
                "name": entity.name, "qualified_name": entity.qualified_name,
                "path": entity.provenance.path, "callers": count, "kind": entity.kind.value,
                "summary": entity.attributes.get("summary", ""),
            })
    return key_symbols


def repo_overview(graph: GraphStore, metadata: MetadataStore, repo_id: str) -> dict:
    kind_counts: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    modules: list[Entity] = []
    externals: list[Entity] = []
    paths_by_id: dict[str, str] = {}

    for entity in graph.entities(repo_id=repo_id):
        kind_counts[entity.kind.value] += 1
        paths_by_id[entity.id] = entity.provenance.path
        if entity.kind == EntityType.FILE and entity.language:
            languages[entity.language] += 1
        elif entity.kind == EntityType.MODULE:
            modules.append(entity)
        elif entity.kind == EntityType.PACKAGE and entity.attributes.get("external"):
            externals.append(entity)

    key_symbols = _rank_key_symbols(graph, paths_by_id)

    module_summaries = []
    for module in modules:
        defines = graph.out_relationships(module.id, [RelationType.DEFINES])
        module_summaries.append({
            "qualified_name": module.qualified_name,
            "path": module.provenance.path,
            "symbols": len(defines),
            "language": module.language,
        })
    module_summaries.sort(key=lambda m: m["symbols"], reverse=True)

    entry_points = _entry_points(graph, repo_id)
    repo = metadata.get_repository(repo_id) or {}
    last = metadata.get_state(repo_id, "last_index", {})

    return {
        "repo_id": repo_id,
        "name": repo.get("name", ""),
        "root": repo.get("root", ""),
        "counts": dict(kind_counts),
        "languages": dict(languages),
        "totals": {
            "files": kind_counts.get("file", 0),
            "modules": kind_counts.get("module", 0),
            "functions": kind_counts.get("function", 0) + kind_counts.get("method", 0),
            "classes": kind_counts.get("class", 0),
            "tests": kind_counts.get("test", 0),
            "config_keys": kind_counts.get("config_key", 0),
        },
        "external_dependencies": sorted({e.name for e in externals}),
        "key_symbols": key_symbols,
        "modules": module_summaries[:50],
        "entry_points": entry_points,
        "last_index": last,
    }


def _entry_points(graph: GraphStore, repo_id: str) -> list[dict]:
    points: list[dict] = []
    for entity in graph.entities(repo_id=repo_id):
        if entity.kind not in (EntityType.FUNCTION, EntityType.METHOD):
            continue
        name = entity.name.lower()
        path = entity.provenance.path.lower()
        if name in ("main", "__main__", "run", "start", "handler", "app") or path.endswith(
            ("main.py", "cli.py", "__main__.py", "index.ts", "index.js", "server.py")
        ):
            points.append({
                "name": entity.name, "qualified_name": entity.qualified_name,
                "path": entity.provenance.path,
            })
    return points[:20]


def explain_module(graph: GraphStore, metadata: MetadataStore, repo_id: str, query: str) -> dict:
    module = _find_module(graph, repo_id, query)
    if module is None:
        return {"ok": False, "error": {"code": "not_found", "message": f"module not found: {query}"}}

    defines = graph.out_relationships(module.id, [RelationType.DEFINES, RelationType.CONTAINS])
    symbols = []
    for rel in defines:
        sym = graph.get_entity(rel.dst_id)
        if sym and sym.kind in SYMBOL_KINDS:
            symbols.append({
                "name": sym.name, "qualified_name": sym.qualified_name, "kind": sym.kind.value,
                "start_line": sym.provenance.start_line, "docstring": sym.attributes.get("docstring", "")[:200],
            })

    imports = [
        _entity_ref(graph.get_entity(r.dst_id))
        for r in graph.out_relationships(module.id, [RelationType.IMPORTS])
    ]
    importers = [
        _entity_ref(graph.get_entity(r.src_id))
        for r in graph.in_relationships(module.id, [RelationType.IMPORTS])
    ]
    from .architecture import layer_for_path

    return {
        "ok": True,
        "module": module.qualified_name,
        "path": module.provenance.path,
        "language": module.language,
        "layer": layer_for_path(module.provenance.path)[0],
        "symbol_count": len(symbols),
        "symbols": symbols,
        "imports": [i for i in imports if i],
        "imported_by": [i for i in importers if i],
        "purpose": _infer_purpose(module, symbols),
    }


def _find_module(graph: GraphStore, repo_id: str, query: str) -> Entity | None:
    for kind in (EntityType.MODULE, EntityType.FILE):
        for entity in graph.entities(kind=kind, repo_id=repo_id):
            if query in (entity.qualified_name, entity.provenance.path, entity.name):
                return entity
    # fuzzy
    q = query.lower()
    for entity in graph.entities(kind=EntityType.MODULE, repo_id=repo_id):
        if q in entity.qualified_name.lower() or q in entity.provenance.path.lower():
            return entity
    return None


def _infer_purpose(module: Entity, symbols: list[dict]) -> str:
    from .architecture import layer_for_path

    layer, desc = layer_for_path(module.provenance.path)
    classes = [s for s in symbols if s["kind"] == "class"]
    funcs = [s for s in symbols if s["kind"] in ("function", "method")]
    parts = [f"{layer} module"]
    if classes:
        parts.append(f"defining {len(classes)} class(es)")
    if funcs:
        parts.append(f"and {len(funcs)} function(s)")
    return " ".join(parts) + f". {desc}"


def _entity_ref(entity: Entity | None) -> dict | None:
    if entity is None:
        return None
    return {
        "name": entity.name, "qualified_name": entity.qualified_name,
        "kind": entity.kind.value, "path": entity.provenance.path,
        "external": bool(entity.attributes.get("external")),
    }


__all__ = ["repo_overview", "explain_module"]
