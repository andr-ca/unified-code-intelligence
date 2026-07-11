"""Optional LLM pass: link *unlinked* DOC_SECTIONs to the code artifacts they describe.

For each DOC_SECTION with no deterministic DESCRIBES edge, the model is shown the section text and
a bounded inventory of candidate names (programs, jobs, transactions, tables) and asked which the
section is *about*. Every returned name is validated against the index (hallucinations dropped) and
written as a DESCRIBES edge with ``extractor="llm:<model>"``, ``resolution="llm-suggested"``,
``confidence=0.6`` — outside ``RESOLVED_LEVELS``, so it lives in the candidates stratum and never
touches impact/completeness (docs/llm-enrichment.md). Cached by section content hash.
"""

from __future__ import annotations

import hashlib

from ..core.entities import EntityType
from ..core.ids import relationship_id
from ..core.provenance import Provenance
from ..core.relationships import Relationship, RelationType
from .llm_client import LlmError

_SYS_DOC_LINKS = (
    "You are linking one documentation section to the code artifacts it describes. "
    "Reply with STRICT JSON only: {\"describes\": [str]}. Choose ONLY names from the provided "
    "inventory that this section is ABOUT — not names merely mentioned in passing. "
    "If the section describes no specific artifact, reply {\"describes\": []}. No markdown."
)

_CANDIDATE_KINDS = (EntityType.LEGACY_PROGRAM, EntityType.JCL_JOB,
                    EntityType.TRANSACTION_CODE, EntityType.DATABASE_TABLE)

#: resolutions that count as an existing deterministic link (so the section is already handled)
_DETERMINISTIC = frozenset({"doc-path", "doc-heading", "doc-code-span", "doc-mention"})


def _inventory(graph, rid: str) -> dict:
    inv: dict = {}
    for kind in _CANDIDATE_KINDS:
        for ent in graph.entities(kind=kind, repo_id=rid):
            if ent.attributes.get("missing") or ent.attributes.get("external"):
                continue
            inv.setdefault(ent.name, ent)
    return inv


def run_doc_links(enricher, limit: int, force: bool) -> None:
    graph, rid, client = enricher.graph, enricher.repo_id, enricher.client
    inventory = _inventory(graph, rid)
    if not inventory:
        return
    inv_names = sorted(inventory)[:200]
    inv_text = "\n".join(f"- {n} ({inventory[n].kind.value})" for n in inv_names)

    cache = {} if force else enricher.metadata.get_state(rid, "enrich:doc_links", {}) or {}
    chunks_by_entity: dict[str, list] = {}
    for c in enricher.metadata.iter_chunks(rid):
        eid = c.get("entity_id")
        if eid:
            chunks_by_entity.setdefault(eid, []).append(c.get("text", ""))

    done = 0
    for sec in list(graph.entities(kind=EntityType.DOC_SECTION, repo_id=rid)):
        if done >= limit:
            break
        existing = graph.out_relationships(sec.id, [RelationType.DESCRIBES])
        if any(r.attributes.get("resolution") in _DETERMINISTIC for r in existing):
            continue  # already deterministically linked — leave it to the parser
        text = "\n".join(chunks_by_entity.get(sec.id, [])) or sec.attributes.get("heading", sec.name)
        src_hash = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        if cache.get(sec.id) == src_hash:
            enricher.stats.cached += 1
            continue
        cache[sec.id] = src_hash
        try:
            data = client.complete_json(
                _SYS_DOC_LINKS,
                f"Section: {sec.attributes.get('heading', sec.name)}\n\n{text[:1500]}\n\n"
                f"Inventory:\n{inv_text}", max_tokens=200)
        except LlmError as exc:
            enricher.stats.errors.append(f"doc_links {sec.name}: {exc}")
            continue
        names = data.get("describes", []) if isinstance(data, dict) else []
        prov = Provenance(rid, sec.provenance.path, sec.provenance.start_line,
                          sec.provenance.start_line, f"llm:{client.model}", 0.6)
        for name in names:
            tgt = inventory.get(str(name).strip())     # validated against the index
            if tgt is None:
                continue
            graph.add_relationship(Relationship(
                id=relationship_id(RelationType.DESCRIBES, sec.id, tgt.id),
                type=RelationType.DESCRIBES, src_id=sec.id, dst_id=tgt.id, provenance=prov,
                attributes={"resolution": "llm-suggested", "match": "llm",
                            "llm": {"model": client.model, "pass": "doc_links"}}))
            enricher.stats.doc_links += 1
            done += 1
    enricher.metadata.set_state(rid, "enrich:doc_links", cache)


__all__ = ["run_doc_links"]
