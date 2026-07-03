"""Dashboard bridge to the optional LLM enrichment layer (``uci.enrich``, docs/llm-enrichment.md).

Driven straight from the stable ``uci.enrich`` package (Enricher + LlmClient) rather than the
engine, so it composes the project's own config/graph/stores. Every fact enrichment writes is labeled
``extractor="llm:<model>"`` with confidence < 1.0 (candidates use ``resolution="llm-suggested"``,
outside ``RESOLVED_LEVELS``) — completeness and the resolution ladder stay honest.
"""

from __future__ import annotations

PASSES = ("summaries", "capabilities", "candidates", "fields", "architecture")


def status(engine) -> dict:
    """LLM provider status + how many entities already carry an enriched summary."""
    from ..enrich import LlmClient

    info: dict = {"protocol": None, "model": "", "base_url": "", "available": False,
                  "configured": False, "summaries": _summary_count(engine)}
    try:
        client = LlmClient(engine.config)
        info.update(protocol=client.protocol, model=client.model, base_url=client.base_url,
                    configured=True, available=bool(client.available))
    except Exception as exc:  # unknown protocol / unreachable / missing config field
        info["error"] = str(exc)
    return info


def _summary_count(engine) -> int:
    try:
        return sum(1 for e in engine.graph.entities(repo_id=engine.repo_id)
                   if e.attributes.get("summary"))
    except Exception:
        return 0


def run(engine, passes, limit: int = 200, force: bool = False) -> dict:
    """Run the requested enrichment passes; returns ``EnrichStats.to_dict()``."""
    from ..enrich import Enricher

    valid = [p for p in (passes or ()) if p in PASSES] or list(PASSES)
    enricher = Enricher(engine.config, engine.graph, engine.metadata, engine.vectors,
                        engine.embedder, engine.repo_id)
    stats = enricher.run(valid, limit=limit, force=force)
    result = stats.to_dict()
    result["passes"] = valid
    return result


def _is_summary_eligible(entity, summary_kinds) -> bool:
    """A summary-bearing kind, excluding config 'modules' (which are not code files)."""
    from ..core.entities import EntityType

    if entity.kind not in summary_kinds:
        return False
    return not (entity.kind == EntityType.MODULE and entity.attributes.get("language") == "config")


def _tally_entity(entity, c: dict, summary_kinds) -> None:
    """Fold one entity into the coverage counters ``c``."""
    from ..core.entities import EntityType

    if entity.kind == EntityType.BUSINESS_CAPABILITY:
        c["capabilities"] += 1
    if entity.attributes.get("missing") or entity.attributes.get("external"):
        return
    if entity.kind == EntityType.LEGACY_PROGRAM:
        c["programs"].add(entity.id)
    if entity.kind == EntityType.COPYBOOK:
        c["copybooks"] += 1
        if entity.attributes.get("data_dictionary"):
            c["with_dict"] += 1
    if _is_summary_eligible(entity, summary_kinds):
        c["eligible"] += 1
        summary = entity.attributes.get("summary")
        if summary:
            c["covered"] += 1
            c["total_len"] += len(str(summary))


def _scan_entity_coverage(graph, rid) -> dict:
    """Per-entity enrichment coverage: summary eligibility/coverage, capability & copybook counts."""
    from ..core.entities import EntityType

    summary_kinds = {EntityType.LEGACY_PROGRAM, EntityType.COPYBOOK, EntityType.JCL_JOB,
                     EntityType.TRANSACTION_CODE, EntityType.MODULE}
    c = {"eligible": 0, "covered": 0, "total_len": 0, "capabilities": 0,
         "copybooks": 0, "with_dict": 0, "programs": set()}
    for entity in graph.entities(repo_id=rid):
        _tally_entity(entity, c, summary_kinds)
    return c


def _scan_edge_honesty(graph) -> dict:
    """llm-suggested edge precision + the honesty invariant (no leak into RESOLVED_LEVELS)."""
    from ..core.relationships import RESOLVED_LEVELS, RelationType

    e = {"llm_edges": 0, "valid_targets": 0, "leaked": 0, "mapped": set()}
    for rel in graph.relationships():
        resolution = rel.attributes.get("resolution")
        if resolution == "llm-suggested":
            e["llm_edges"] += 1
            dst = graph.get_entity(rel.dst_id)
            if dst and not dst.attributes.get("missing") and not dst.attributes.get("external"):
                e["valid_targets"] += 1
            if resolution in RESOLVED_LEVELS:  # invariant: never true
                e["leaked"] += 1
        if rel.type == RelationType.IMPLEMENTS_CAPABILITY:
            e["mapped"].add(rel.src_id)
    return e


def evaluate(engine) -> dict:
    """Score the LLM enrichment already in the graph — coverage per pass, candidate precision
    (llm-suggested edges that actually hit an indexed target), and the **honesty** invariant:
    no ``llm-suggested`` fact may leak into ``RESOLVED_LEVELS`` (it stays candidate-only)."""
    graph, rid = engine.graph, engine.repo_id
    c = _scan_entity_coverage(graph, rid)
    e = _scan_edge_honesty(graph)
    arch = engine.metadata.get_state(rid, "architecture_summary", {}) or {}
    llm_edges = e["llm_edges"]

    def _cov(num, den):
        return round(num / den, 3) if den else 0.0

    return {
        "summaries": {"eligible": c["eligible"], "covered": c["covered"],
                      "coverage": _cov(c["covered"], c["eligible"]),
                      "avg_chars": round(c["total_len"] / c["covered"]) if c["covered"] else 0},
        "capabilities": {"count": c["capabilities"], "programs": len(c["programs"]),
                         "mapped": len(e["mapped"]), "coverage": _cov(len(e["mapped"]), len(c["programs"]))},
        "candidates": {"edges": llm_edges, "valid_targets": e["valid_targets"],
                       "precision": round(e["valid_targets"] / llm_edges, 3) if llm_edges else 1.0},
        "fields": {"copybooks": c["copybooks"], "with_dictionary": c["with_dict"],
                   "coverage": _cov(c["with_dict"], c["copybooks"])},
        "architecture": {"present": bool(arch.get("overview")),
                         "model": (arch.get("llm") or {}).get("model", ""),
                         "key_points": len(arch.get("key_points", []))},
        "honesty": {"llm_suggested_edges": llm_edges, "leaked_into_ladder": e["leaked"], "ok": e["leaked"] == 0},
    }


__all__ = ["PASSES", "status", "run", "evaluate"]
