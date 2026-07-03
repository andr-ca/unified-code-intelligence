"""Understand tab — composed narrative + coverage/blind-spots (Engine.understand)."""

from __future__ import annotations

from pathlib import Path

import pytest

from uci import Config, Engine
from uci.core import Entity, EntityType, Provenance, Relationship, RelationType


@pytest.fixture
def seeded(tmp_path: Path):
    repo = tmp_path / "u"
    repo.mkdir()
    eng = Engine(Config.from_env(repo))
    rid = eng.repo_id

    def ent(eid, kind, name, attrs=None, path=None):
        return Entity(eid, kind, name, name, Provenance(rid, path or f"{name}.py", 1, 3), attrs or {})

    # functions: main (entry, excluded), used (called), orphan (uncalled -> possibly unused)
    eng.graph.add_entity(ent("fn:main", EntityType.FUNCTION, "main"))
    eng.graph.add_entity(ent("fn:used", EntityType.FUNCTION, "used"))
    eng.graph.add_entity(ent("fn:orphan", EntityType.FUNCTION, "orphan"))
    eng.graph.add_relationship(Relationship(
        "c1", RelationType.CALLS, "fn:main", "fn:used", Provenance(rid, "main.py", 2, 2)))
    # a file with an unrecognized language (shallow) + a normal one
    eng.graph.add_entity(ent("file:weird", EntityType.FILE, "weird.bin",
                             {"language": "unknown"}, path="weird.bin"))
    eng.graph.add_entity(ent("file:app", EntityType.FILE, "app.py",
                             {"language": "python"}, path="app.py"))
    yield eng
    eng.close()


def test_understand_structural_only(seeded):
    u = seeded.understand()
    assert u["ok"] and u["enriched"] is False
    # composed sections are present
    assert set(u) >= {"summary", "organization", "execution", "key_parts", "reading_path", "coverage"}
    assert u["summary"]["totals"]["functions"] == 3
    assert u["summary"]["purpose"] == []  # no enrichment yet


def test_understand_coverage_heuristics(seeded):
    cov = seeded.understand()["coverage"]
    unused = {u["name"] for u in cov["possibly_unused"]}
    assert "orphan" in unused          # nothing references it
    assert "used" not in unused        # called by main
    assert "main" not in unused        # entry-name excluded
    shallow = {s["path"] for s in cov["shallow_files"]}
    assert "weird.bin" in shallow      # unknown language
    assert "app.py" not in shallow     # recognized language


def test_understand_reflects_enrichment(seeded):
    # add an LLM capability -> the domain layer lights up
    rid = seeded.repo_id
    seeded.graph.add_entity(Entity(
        "cap:billing", EntityType.BUSINESS_CAPABILITY, "Billing", "Billing",
        Provenance(rid, "", 0, 0, f"llm:x", 0.7), {"description": "Handles invoicing"}))
    seeded.graph.add_relationship(Relationship(
        "impl1", RelationType.IMPLEMENTS_CAPABILITY, "fn:used", "cap:billing",
        Provenance(rid, "", 0, 0)))
    u = seeded.understand()
    assert u["enriched"] is True
    assert any(p["name"] == "Billing" for p in u["summary"]["purpose"])
    assert any(c["name"] == "Billing" for c in u["organization"]["capabilities"])


def test_understand_walkthrough_traces_a_thread(seeded):
    w = seeded.understand()["walkthrough"]
    assert w["entry"]["name"] == "main"   # the only entry point in the seed
    assert w["same"] is True              # main is its own target
    assert "used" in {c["name"] for c in w["calls"]}  # traces main -> used
