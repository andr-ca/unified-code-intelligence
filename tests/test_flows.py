"""Flows tab — business-capability traversal (``Engine.flows``).

Seeds the graph directly (no LLM, no parser) so the full business story — implementing programs,
triggers (transaction codes / JCL jobs) and data (READS/WRITES tables) — is exercised deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uci import Config, Engine
from uci.core import Entity, EntityType, Provenance, Relationship, RelationType


@pytest.fixture
def flow_engine(tmp_path: Path):
    repo = tmp_path / "flowrepo"
    repo.mkdir()
    eng = Engine(Config.from_env(repo, {"embedding_provider": "local"}))
    rid = eng.repo_id

    def ent(eid, kind, name, attrs=None):
        return Entity(eid, kind, name, name, Provenance(rid, f"{name}.cbl", 1, 3), attrs or {})

    def rel(rel_id, rtype, src, dst):
        return Relationship(rel_id, rtype, src, dst, Provenance(rid, "x", 1, 1))

    # one capability, two implementing programs (one carries a summary)
    eng.graph.add_entity(ent("cap:payments", EntityType.BUSINESS_CAPABILITY, "Payments",
                             {"description": "Handles customer payments"}))
    eng.graph.add_entity(ent("prog:PAYPROC", EntityType.LEGACY_PROGRAM, "PAYPROC",
                             {"summary": "Posts a payment to the ledger"}))
    eng.graph.add_entity(ent("prog:PAYVAL", EntityType.LEGACY_PROGRAM, "PAYVAL"))
    # entry points + data
    eng.graph.add_entity(ent("txn:PAY0", EntityType.TRANSACTION_CODE, "PAY0"))
    eng.graph.add_entity(ent("jcl:PAYJOB", EntityType.JCL_JOB, "PAYJOB"))
    eng.graph.add_entity(ent("tbl:LEDGER", EntityType.DATABASE_TABLE, "LEDGER"))

    eng.graph.add_relationship(rel("i1", RelationType.IMPLEMENTS_CAPABILITY, "prog:PAYPROC", "cap:payments"))
    eng.graph.add_relationship(rel("i2", RelationType.IMPLEMENTS_CAPABILITY, "prog:PAYVAL", "cap:payments"))
    eng.graph.add_relationship(rel("t1", RelationType.INVOKES, "txn:PAY0", "prog:PAYPROC"))
    eng.graph.add_relationship(rel("t2", RelationType.RUNS, "jcl:PAYJOB", "prog:PAYVAL"))
    eng.graph.add_relationship(rel("c1", RelationType.CALLS, "prog:PAYPROC", "prog:PAYVAL"))
    eng.graph.add_relationship(rel("d1", RelationType.WRITES, "prog:PAYPROC", "tbl:LEDGER"))
    yield eng
    eng.close()


def test_flows_empty_state_when_unenriched(tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    with Engine(Config.from_env(repo, {"embedding_provider": "local"})) as eng:
        out = eng.flows()
    assert out["ok"] is True
    assert out["enriched"] is False
    assert out["capabilities"] == []


def test_flows_full_business_story(flow_engine):
    out = flow_engine.flows()
    assert out["ok"] and out["enriched"] is True
    assert len(out["capabilities"]) == 1
    cap = out["capabilities"][0]
    assert cap["name"] == "Payments"
    assert cap["description"] == "Handles customer payments"

    # implementing programs (with their summaries)
    assert {p["name"] for p in cap["programs"]} == {"PAYPROC", "PAYVAL"}
    payproc = next(p for p in cap["programs"] if p["name"] == "PAYPROC")
    assert payproc["summary"] == "Posts a payment to the ledger"

    # triggers: PAY0 invokes PAYPROC; PAYJOB runs PAYVAL
    assert {t["name"] for t in cap["triggers"]} == {"PAY0", "PAYJOB"}

    # data: PAYPROC writes LEDGER
    assert {d["name"]: d["access"] for d in cap["data"]} == {"LEDGER": "write"}


def test_flows_read_and_write_access_merged(flow_engine):
    # a second edge makes LEDGER both read and written by a member program
    flow_engine.graph.add_relationship(Relationship(
        "d2", RelationType.READS, "prog:PAYVAL", "tbl:LEDGER",
        Provenance(flow_engine.repo_id, "x", 1, 1)))
    cap = flow_engine.flows()["capabilities"][0]
    assert {d["name"]: d["access"] for d in cap["data"]} == {"LEDGER": "read/write"}


def test_flows_excludes_missing_and_external_programs(flow_engine):
    # stub/external programs mapped to the capability must never surface
    flow_engine.graph.add_entity(Entity(
        "prog:GHOST", EntityType.LEGACY_PROGRAM, "GHOST", "GHOST",
        Provenance(flow_engine.repo_id, "GHOST.cbl", 1, 1), {"missing": True}))
    flow_engine.graph.add_relationship(Relationship(
        "i3", RelationType.IMPLEMENTS_CAPABILITY, "prog:GHOST", "cap:payments",
        Provenance(flow_engine.repo_id, "x", 1, 1)))
    cap = flow_engine.flows()["capabilities"][0]
    assert "GHOST" not in {p["name"] for p in cap["programs"]}
