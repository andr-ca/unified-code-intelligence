"""Guided onboarding generation — a dependency-ordered "start here" reading path (inspired by
Understand-Anything's tour builder), derived from the graph without an LLM.
"""

from __future__ import annotations

from ..core.interfaces import GraphStore, MetadataStore
from .architecture import infer_architecture
from .overview import repo_overview


def onboarding_guide(graph: GraphStore, metadata: MetadataStore, repo_id: str) -> dict:
    overview = repo_overview(graph, metadata, repo_id)
    architecture = infer_architecture(graph, repo_id)

    steps: list[dict] = []
    order = 1
    # 1. entry points first
    for ep in overview["entry_points"][:5]:
        steps.append({
            "order": order, "title": f"Entry point: {ep['name']}",
            "path": ep["path"], "why": "Execution starts here — follow the flow outward.",
        })
        order += 1
    # 2. most-depended-on symbols (high cross-file fan-in)
    for sym in overview["key_symbols"][:5]:
        why = sym.get("summary") or "Widely used — understanding it unlocks much of the codebase."
        steps.append({
            "order": order, "title": f"Core symbol: {sym['name']} ({sym['callers']} callers)",
            "path": sym["path"], "why": why,
        })
        order += 1
    # 3. one representative module per layer (largest)
    for layer in architecture["layers"]:
        if not layer["modules"]:
            continue
        top = layer["modules"][0]
        steps.append({
            "order": order, "title": f"{layer['name']} layer: {top['qualified_name']}",
            "path": top["path"], "why": layer["description"],
        })
        order += 1

    key_concepts = [
        {"layer": layer["name"], "description": layer["description"],
         "module_count": layer["module_count"]}
        for layer in architecture["layers"]
    ]

    return {
        "repo_id": repo_id,
        "name": overview["name"],
        "summary": _summary(overview, architecture),
        "totals": overview["totals"],
        "steps": steps,
        "key_concepts": key_concepts,
        "external_dependencies": overview["external_dependencies"],
        "markdown": _to_markdown(overview, architecture, steps),
    }


def _summary(overview: dict, architecture: dict) -> str:
    langs = ", ".join(sorted(overview["languages"])) or "unknown"
    layer_names = ", ".join(layer["name"] for layer in architecture["layers"]) or "n/a"
    t = overview["totals"]
    return (
        f"{overview['name'] or 'This repository'} contains {t['files']} files "
        f"({langs}) with {t['functions']} functions and {t['classes']} classes across these "
        f"layers: {layer_names}."
    )


def _to_markdown(overview: dict, architecture: dict, steps: list[dict]) -> str:
    lines = [f"# Onboarding: {overview['name'] or overview['repo_id']}", ""]
    lines.append(_summary(overview, architecture))
    lines += ["", "## Suggested reading order", ""]
    for step in steps:
        lines.append(f"{step['order']}. **{step['title']}** — `{step['path']}`  \n   {step['why']}")
    lines += ["", "## Architecture layers", ""]
    for layer in architecture["layers"]:
        lines.append(f"- **{layer['name']}** ({layer['module_count']} modules): {layer['description']}")
    if overview["external_dependencies"]:
        lines += ["", "## External dependencies", "",
                  ", ".join(overview["external_dependencies"])]
    return "\n".join(lines)


__all__ = ["onboarding_guide"]
