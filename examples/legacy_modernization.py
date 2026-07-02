#!/usr/bin/env python3
"""Legacy modernization demo (Phase-5 preview).

Shows that the *same* canonical schema absorbs COBOL/JCL/copybook facts with full provenance — no
special-case storage. A tiny regex extractor turns the sample files in ``examples/legacy/`` into
canonical entities/relationships, loads them into the in-memory graph, and answers modernization
questions: copybook usage, JCL→program execution, change impact, and migration candidates.

Run:  PYTHONPATH=src python3 examples/legacy_modernization.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uci.core.entities import Entity, EntityType
from uci.core.ids import entity_id, relationship_id
from uci.core.provenance import Provenance
from uci.core.relationships import Relationship, RelationType
from uci.graph.inmemory import InMemoryGraphStore

REPO = "legacy-demo"
LEGACY_DIR = Path(__file__).parent / "legacy"


def _prov(path: str, line: int) -> Provenance:
    return Provenance(REPO, path, line, line, "legacy_extractor", 0.9)


def _ent(kind, path, qname, name, line, attrs=None):
    return Entity(entity_id(kind, REPO, path, qname), kind, name, qname, _prov(path, line), attrs or {})


def _rel(rtype, src, dst, path, line, attrs=None):
    return Relationship(relationship_id(rtype, src, dst), rtype, src, dst, _prov(path, line), attrs or {})


def extract(graph: InMemoryGraphStore) -> None:
    entities: list[Entity] = []
    rels: list[Relationship] = []

    # --- COBOL program (order.cbl) ---
    cbl = (LEGACY_DIR / "order.cbl").read_text().splitlines()
    program_id = None
    program_ent = None
    for i, line in enumerate(cbl, 1):
        m = re.search(r"PROGRAM-ID\.\s+(\S+?)\.", line)
        if m:
            program_id = m.group(1)
            program_ent = _ent(EntityType.LEGACY_PROGRAM, "order.cbl", program_id, program_id, i)
            entities.append(program_ent)
    # copybook usage (COPY X)
    for i, line in enumerate(cbl, 1):
        m = re.search(r"\bCOPY\s+(\S+?)\.", line)
        if m and program_ent:
            cpy = m.group(1)
            cpy_ent = _ent(EntityType.COPYBOOK, f"{cpy}.cpy", cpy, cpy, 1)
            entities.append(cpy_ent)
            rels.append(_rel(RelationType.DEPENDS_ON, program_ent.id, cpy_ent.id, "order.cbl", i))
    # paragraphs + PERFORM call graph
    paragraphs: dict[str, Entity] = {}
    in_proc = False
    current = None
    for i, line in enumerate(cbl, 1):
        if "PROCEDURE DIVISION" in line:
            in_proc = True
            continue
        if not in_proc or not program_ent:
            continue
        pm = re.match(r"\s{7}([A-Z0-9-]+)\.\s*$", line)
        if pm:
            name = pm.group(1)
            para = _ent(EntityType.PARAGRAPH, "order.cbl", f"{program_id}.{name}", name, i)
            paragraphs[name] = para
            entities.append(para)
            rels.append(_rel(RelationType.CONTAINS, program_ent.id, para.id, "order.cbl", i))
            current = para
        perf = re.search(r"\bPERFORM\s+([A-Z0-9-]+)", line)
        if perf and current:
            target = perf.group(1)
            # resolve later; store pending as attribute-free CALLS by name
            rels.append(("PERFORM", current, target, i))

    # resolve PERFORM edges once all paragraphs are known
    resolved: list[Relationship] = []
    for r in rels:
        if isinstance(r, tuple):
            _, src_para, target_name, line = r
            tgt = paragraphs.get(target_name)
            if tgt:
                resolved.append(_rel(RelationType.CALLS, src_para.id, tgt.id, "order.cbl", line))
        else:
            resolved.append(r)
    rels = resolved

    # dataset the program reads (SELECT ... ASSIGN TO)
    for i, line in enumerate(cbl, 1):
        m = re.search(r"SELECT\s+([A-Z0-9-]+)\s+ASSIGN", line)
        if m and program_ent:
            ds = _ent(EntityType.DATABASE_TABLE, "", m.group(1), m.group(1), i, {"legacy_dataset": True})
            entities.append(ds)
            rels.append(_rel(RelationType.READS, program_ent.id, ds.id, "order.cbl", i))

    # --- Copybook fields (ORDREC.cpy) + MAPS_TO db columns ---
    cpy_lines = (LEGACY_DIR / "ORDREC.cpy").read_text().splitlines()
    for i, line in enumerate(cpy_lines, 1):
        fm = re.match(r"\s+\d\d\s+([A-Z0-9-]+)\s+PIC", line)
        if fm:
            field = fm.group(1)
            fld = _ent(EntityType.VARIABLE, "ORDREC.cpy", f"ORDREC.{field}", field, i, {"copybook_field": True})
            entities.append(fld)
            # mock mapping to a modern DB column
            col = _ent(EntityType.DATABASE_COLUMN, "", f"orders.{field.lower().replace('ord-', '')}",
                       field.lower().replace("ord-", ""), i)
            entities.append(col)
            rels.append(_rel(RelationType.MAPS_TO, fld.id, col.id, "ORDREC.cpy", i))

    # --- JCL job (RUNORDER.jcl) runs the program ---
    jcl = (LEGACY_DIR / "RUNORDER.jcl").read_text().splitlines()
    job_ent = None
    for i, line in enumerate(jcl, 1):
        jm = re.match(r"//(\S+)\s+JOB", line)
        if jm:
            job_ent = _ent(EntityType.JCL_JOB, "RUNORDER.jcl", jm.group(1), jm.group(1), i)
            entities.append(job_ent)
        em = re.search(r"EXEC\s+PGM=(\S+)", line)
        if em and job_ent and program_ent:
            rels.append(_rel(RelationType.RUNS, job_ent.id, program_ent.id, "RUNORDER.jcl", i))

    # --- migration candidate ---
    if program_ent:
        service = _ent(EntityType.SERVICE, "", "order-service", "order-service", 0)
        entities.append(service)
        rels.append(_rel(RelationType.CANDIDATE_FOR_MIGRATION, program_ent.id, service.id, "order.cbl", 1))

    graph.add_entities(entities)
    graph.add_relationships([r for r in rels if isinstance(r, Relationship)])


def _fmt(entity: Entity) -> str:
    loc = entity.provenance.location()
    return f"{entity.kind.value:<16} {entity.qualified_name:<28} ({loc})"


def main() -> None:
    graph = InMemoryGraphStore()
    extract(graph)

    print("=" * 74)
    print("LEGACY MODERNIZATION DEMO — canonical schema over COBOL/JCL/copybook")
    print("=" * 74)

    prog = graph.find_by_name("ORDERPGM", exact=True)[0]

    print("\n1. Copybooks / datasets ORDERPGM depends on:")
    for rel, nb in graph.neighbors(prog.id, "out", [RelationType.DEPENDS_ON, RelationType.READS]):
        print(f"   {rel.type.value:<11} -> {_fmt(nb)}")

    print("\n2. Paragraphs (PERFORM call graph) inside ORDERPGM:")
    for rel, nb in graph.neighbors(prog.id, "out", [RelationType.CONTAINS]):
        callees = graph.out_relationships(nb.id, [RelationType.CALLS])
        tail = " -> " + ", ".join(graph.get_entity(c.dst_id).name for c in callees) if callees else ""
        print(f"   {nb.name}{tail}")

    print("\n3. What JCL runs ORDERPGM? (reverse RUNS — impact of a program change):")
    for rel, nb in graph.neighbors(prog.id, "in", [RelationType.RUNS]):
        print(f"   {_fmt(nb)}")

    print("\n4. Copybook field -> modern DB column mappings (data lineage):")
    for rel in graph.relationships(RelationType.MAPS_TO):
        src, dst = graph.get_entity(rel.src_id), graph.get_entity(rel.dst_id)
        print(f"   {src.name:<14} MAPS_TO  {dst.qualified_name}   ({rel.provenance.location()})")

    print("\n5. Migration candidates:")
    for rel in graph.relationships(RelationType.CANDIDATE_FOR_MIGRATION):
        src, dst = graph.get_entity(rel.src_id), graph.get_entity(rel.dst_id)
        print(f"   {src.qualified_name} -> {dst.qualified_name}  ({rel.provenance.location()})")

    print(f"\nGraph: {graph.count_entities()} entities, {graph.count_relationships()} relationships. "
          "Every fact traces to a file:line.")


if __name__ == "__main__":
    main()
