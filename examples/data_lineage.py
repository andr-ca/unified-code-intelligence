#!/usr/bin/env python3
"""Data-lineage demo (Phase-4 preview).

Builds a small graph of functions reading/writing tables, a SQL query touching columns, and a
DTO→entity mapping — then answers "who touches the ``orders`` table?" via graph traversal. Shows the
canonical data relationships (READS/WRITES/MAPS_TO) that Phase-4 SQL extractors will populate
automatically.

Run:  PYTHONPATH=src python3 examples/data_lineage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uci.core.entities import Entity, EntityType
from uci.core.ids import entity_id, relationship_id
from uci.core.provenance import Provenance
from uci.core.relationships import Relationship, RelationType
from uci.graph.inmemory import InMemoryGraphStore

REPO = "lineage-demo"


def ent(kind, path, qname, line):
    return Entity(entity_id(kind, REPO, path, qname), kind, qname.split(".")[-1], qname,
                  Provenance(REPO, path, line, line, "sql_extractor", 0.8), {})


def rel(rtype, s, d, path, line):
    return Relationship(relationship_id(rtype, s.id, d.id), rtype, s.id, d.id,
                        Provenance(REPO, path, line, line, "sql_extractor", 0.8), {})


def build(graph: InMemoryGraphStore) -> None:
    place_order = ent(EntityType.FUNCTION, "orders/service.py", "orders.service.place_order", 12)
    load_report = ent(EntityType.FUNCTION, "reports/daily.py", "reports.daily.load_report", 30)
    orders_tbl = ent(EntityType.DATABASE_TABLE, "", "orders", 0)
    total_col = ent(EntityType.DATABASE_COLUMN, "", "orders.total", 0)
    report = ent(EntityType.REPORT, "", "DailySalesReport", 0)
    query = ent(EntityType.QUERY, "reports/daily.py", "reports.daily.sales_query", 33)
    dto = ent(EntityType.DTO, "api/models.py", "api.models.OrderDTO", 8)

    graph.add_entities([place_order, load_report, orders_tbl, total_col, report, query, dto])
    graph.add_relationships([
        rel(RelationType.WRITES, place_order, orders_tbl, "orders/service.py", 15),
        rel(RelationType.READS, load_report, orders_tbl, "reports/daily.py", 34),
        rel(RelationType.READS, query, total_col, "reports/daily.py", 33),
        rel(RelationType.DEPENDS_ON, report, query, "reports/daily.py", 31),
        rel(RelationType.MAPS_TO, dto, orders_tbl, "api/models.py", 8),
    ])


def main() -> None:
    graph = InMemoryGraphStore()
    build(graph)
    orders = graph.find_by_name("orders", exact=True)[0]

    print("=" * 66)
    print("DATA-LINEAGE DEMO — who touches the `orders` table?")
    print("=" * 66)

    print("\nWriters (WRITES -> orders):")
    for r, nb in graph.neighbors(orders.id, "in", [RelationType.WRITES]):
        print(f"   {nb.qualified_name}  ({r.provenance.location()})")

    print("\nReaders (READS -> orders):")
    for r, nb in graph.neighbors(orders.id, "in", [RelationType.READS]):
        print(f"   {nb.qualified_name}  ({r.provenance.location()})")

    print("\nMapped DTOs / API models (MAPS_TO -> orders):")
    for r, nb in graph.neighbors(orders.id, "in", [RelationType.MAPS_TO]):
        print(f"   {nb.qualified_name}  ({r.provenance.location()})")

    print("\nDownstream reports depending on queries that read orders:")
    for r in graph.relationships(RelationType.DEPENDS_ON):
        report, q = graph.get_entity(r.src_id), graph.get_entity(r.dst_id)
        if any(rr.dst_id and graph.get_entity(rr.dst_id) and graph.get_entity(rr.dst_id).qualified_name.startswith("orders")
               for rr in graph.out_relationships(q.id, [RelationType.READS])):
            print(f"   {report.qualified_name} depends on {q.qualified_name}")


if __name__ == "__main__":
    main()
