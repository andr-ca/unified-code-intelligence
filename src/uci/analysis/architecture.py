"""Architecture / layer inference from the canonical graph (heuristic, LLM-free).

Assigns each module to an architectural layer and aggregates inter-layer dependencies so the
dashboard and MCP ``explain_module`` can show a "how is this organized?" view.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ..core.entities import EntityType
from ..core.interfaces import GraphStore
from ..core.relationships import RelationType

# ordered: first match wins (checked against path + module name tokens)
_LAYER_RULES: list[tuple[str, tuple[str, ...], str]] = [
    ("Test", ("test", "tests", "spec", "__tests__", "conftest"), "Automated tests and fixtures."),
    ("API", ("api", "route", "routes", "controller", "controllers", "endpoint", "endpoints", "handler", "handlers", "views", "rest", "graphql"), "HTTP endpoints, route handlers, and controllers."),
    ("UI", ("ui", "component", "components", "page", "pages", "view", "frontend", "web", "widgets"), "User-interface components and views."),
    ("Service", ("service", "services", "usecase", "usecases", "domain", "logic", "business", "core", "engine", "manager"), "Business logic and use cases."),
    ("Data", ("model", "models", "repository", "repositories", "dao", "db", "database", "store", "stores", "entity", "entities", "schema", "migrations", "orm"), "Data models, persistence, and database access."),
    ("Config", ("config", "settings", "conf", "env", "constants"), "Configuration and environment."),
    ("Utility", ("util", "utils", "helper", "helpers", "common", "shared", "lib", "tools"), "Shared utilities and helpers."),
]
_DEFAULT_LAYER = ("Core", "Core application modules.")


def layer_for_path(path: str) -> tuple[str, str]:
    tokens = set(path.lower().replace("\\", "/").replace(".", "/").split("/"))
    for name, keywords, desc in _LAYER_RULES:
        if tokens & set(keywords):
            return name, desc
    return _DEFAULT_LAYER


def infer_architecture(graph: GraphStore, repo_id: str) -> dict:
    layer_modules: dict[str, list[dict]] = defaultdict(list)
    module_layer: dict[str, str] = {}
    descriptions: dict[str, str] = {}

    for module in graph.entities(kind=EntityType.MODULE, repo_id=repo_id):
        layer, desc = layer_for_path(module.provenance.path)
        descriptions[layer] = desc
        module_layer[module.id] = layer
        symbol_count = len(graph.out_relationships(module.id, [RelationType.DEFINES]))
        layer_modules[layer].append({
            "qualified_name": module.qualified_name,
            "path": module.provenance.path,
            "symbols": symbol_count,
        })

    # aggregate inter-layer dependency weights from module IMPORTS
    edge_weights: Counter[tuple[str, str]] = Counter()
    for rel in graph.relationships(RelationType.IMPORTS):
        src_layer = module_layer.get(rel.src_id)
        dst_layer = module_layer.get(rel.dst_id)
        if src_layer and dst_layer and src_layer != dst_layer:
            edge_weights[(src_layer, dst_layer)] += 1

    layers = [
        {
            "name": name,
            "description": descriptions.get(name, ""),
            "module_count": len(mods),
            "modules": sorted(mods, key=lambda m: m["symbols"], reverse=True),
        }
        for name, mods in sorted(layer_modules.items(), key=lambda kv: -len(kv[1]))
    ]
    edges = [
        {"source": s, "target": t, "weight": w}
        for (s, t), w in edge_weights.most_common()
    ]
    return {"repo_id": repo_id, "layers": layers, "edges": edges}


__all__ = ["infer_architecture", "layer_for_path"]
