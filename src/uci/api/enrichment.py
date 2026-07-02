"""Dashboard bridge to the optional LLM enrichment layer (``uci.enrich``, docs/llm-enrichment.md).

Driven straight from the stable ``uci.enrich`` package (Enricher + LlmClient) rather than the
engine, so it composes the project's own config/graph/stores. Every fact enrichment writes is labeled
``extractor="llm:<model>"`` with confidence < 1.0 (candidates use ``resolution="llm-suggested"``,
outside ``RESOLVED_LEVELS``) — completeness and the resolution ladder stay honest.
"""

from __future__ import annotations

PASSES = ("summaries", "capabilities", "candidates", "fields")


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


def evaluate(engine) -> dict:
    """Score the LLM enrichment already in the graph — coverage per pass, candidate precision
    (llm-suggested edges that actually hit an indexed target), and the **honesty** invariant:
    no ``llm-suggested`` fact may leak into ``RESOLVED_LEVELS`` (it stays candidate-only)."""
    from ..core.entities import EntityType
    from ..core.relationships import RESOLVED_LEVELS, RelationType

    graph, rid = engine.graph, engine.repo_id
    summary_kinds = {EntityType.LEGACY_PROGRAM, EntityType.COPYBOOK, EntityType.JCL_JOB,
                     EntityType.TRANSACTION_CODE, EntityType.MODULE}
    eligible = covered = total_len = capabilities = copybooks = with_dict = 0
    programs: set = set()
    for entity in graph.entities(repo_id=rid):
        if entity.kind == EntityType.BUSINESS_CAPABILITY:
            capabilities += 1
        if entity.attributes.get("missing") or entity.attributes.get("external"):
            continue
        if entity.kind == EntityType.LEGACY_PROGRAM:
            programs.add(entity.id)
        if entity.kind == EntityType.COPYBOOK:
            copybooks += 1
            if entity.attributes.get("data_dictionary"):
                with_dict += 1
        if entity.kind in summary_kinds and not (
                entity.kind == EntityType.MODULE and entity.attributes.get("language") == "config"):
            eligible += 1
            summary = entity.attributes.get("summary")
            if summary:
                covered += 1
                total_len += len(str(summary))

    llm_edges = valid_targets = leaked = 0
    mapped: set = set()
    for rel in graph.relationships():
        resolution = rel.attributes.get("resolution")
        if resolution == "llm-suggested":
            llm_edges += 1
            dst = graph.get_entity(rel.dst_id)
            if dst and not dst.attributes.get("missing") and not dst.attributes.get("external"):
                valid_targets += 1
            if resolution in RESOLVED_LEVELS:  # invariant: never true
                leaked += 1
        if rel.type == RelationType.IMPLEMENTS_CAPABILITY:
            mapped.add(rel.src_id)

    def _cov(num, den):
        return round(num / den, 3) if den else 0.0

    return {
        "summaries": {"eligible": eligible, "covered": covered, "coverage": _cov(covered, eligible),
                      "avg_chars": round(total_len / covered) if covered else 0},
        "capabilities": {"count": capabilities, "programs": len(programs), "mapped": len(mapped),
                         "coverage": _cov(len(mapped), len(programs))},
        "candidates": {"edges": llm_edges, "valid_targets": valid_targets,
                       "precision": round(valid_targets / llm_edges, 3) if llm_edges else 1.0},
        "fields": {"copybooks": copybooks, "with_dictionary": with_dict,
                   "coverage": _cov(with_dict, copybooks)},
        "honesty": {"llm_suggested_edges": llm_edges, "leaked_into_ladder": leaked, "ok": leaked == 0},
    }


__all__ = ["PASSES", "status", "run", "evaluate"]
