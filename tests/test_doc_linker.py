from conftest import _repo

from uci import Config, Engine
from uci.core.entities import EntityType
from uci.core.relationships import RelationType


def _describes(engine):
    return list(engine.graph.relationships(RelationType.DESCRIBES))


def test_doc_section_entities_and_describes_edges(tmp_path):
    with Engine(Config.from_env(_repo(tmp_path))) as eng:
        eng.index(full=True)
        sections = list(eng.graph.entities(kind=EntityType.DOC_SECTION))
        assert {s.name for s in sections} == {"App", "Signon — COSGN00C"}
        edges = _describes(eng)
        by = {(eng.graph.get_entity(r.src_id).name, eng.graph.get_entity(r.dst_id).name,
               r.attributes.get("resolution")) for r in edges}
        assert ("Signon — COSGN00C", "COSGN00C", "doc-heading") in by
        assert ("Signon — COSGN00C", "COSGN00C.cbl", "doc-path") in by


def test_documented_but_missing_member_becomes_gap(tmp_path):
    with Engine(Config.from_env(_repo(tmp_path))) as eng:
        eng.index(full=True)
        gaps = eng.gaps()["gaps"]
        names = {g["name"] for g in gaps}
        assert "CBTRN99C" in names
        gap = next(g for g in gaps if g["name"] == "CBTRN99C")
        assert "documented-artifact-missing" in gap["reasons"]
        assert "IGNOREME" not in names  # bare-prose miss: dropped, not a gap


def test_describes_never_inflates_impact(tmp_path):
    with Engine(Config.from_env(_repo(tmp_path))) as eng:
        eng.index(full=True)
        pack = eng.impact("COSGN00C")
        callers = pack.get("callers", {})
        flat = []
        if isinstance(callers, dict):
            for bucket in callers.values():
                flat.extend(c.get("name", "") for c in bucket if isinstance(c, dict))
        elif isinstance(callers, list):
            flat = [c.get("name", "") for c in callers if isinstance(c, dict)]
        assert all("Signon" not in c for c in flat)
