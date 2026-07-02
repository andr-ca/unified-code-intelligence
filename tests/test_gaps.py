"""Gap registry tests: missing artifacts become first-class, self-healing facts."""

from __future__ import annotations

import json
from pathlib import Path

from uci import Config, Engine
from uci.cli.main import main
from uci.mcp.server import MCPServer


def _engine(path: Path) -> Engine:
    return Engine(Config.from_env(path))


def test_no_gaps_when_repo_is_complete(sample_repo: Path):
    eng = _engine(sample_repo)
    eng.index(full=True)
    assert eng.gaps()["gaps"] == []  # all internal imports resolve; os/stdlib is external, not a gap
    eng.close()


def test_removing_a_file_creates_a_named_gap(sample_repo: Path):
    eng = _engine(sample_repo)
    eng.index(full=True)
    (sample_repo / "pricing" / "rules.py").unlink()
    eng.index(full=False)

    gaps = eng.gaps()["gaps"]
    record = next((g for g in gaps if g["name"] == "pricing.rules"), None)
    assert record is not None
    assert record["artifact_kind"] == "module"
    assert "pricing/rules" in record["expected_origin"]
    # names every referencing site
    assert any(s["path"] == "pricing/calculator.py" for s in record["referencing_sites"])
    assert record["ref_count"] >= 1
    eng.close()


def test_restoring_the_file_heals_the_gap(sample_repo: Path):
    eng = _engine(sample_repo)
    eng.index(full=True)
    rules = sample_repo / "pricing" / "rules.py"
    content = rules.read_text()
    rules.unlink()
    eng.index(full=False)
    assert eng.gaps()["gaps"]  # gap present
    rules.write_text(content)
    eng.index(full=False)
    assert eng.gaps()["gaps"] == []  # auto-closed on next generation
    eng.close()


def test_external_and_stdlib_imports_are_not_gaps(sample_repo: Path):
    (sample_repo / "uses_ext.py").write_text(
        "import os\nimport requests\n\ndef f():\n    return requests.get('x')\n"
    )
    eng = _engine(sample_repo)
    eng.index(full=True)
    names = {g["name"] for g in eng.gaps()["gaps"]}
    assert "os" not in names and "requests" not in names
    eng.close()


def test_impact_cites_gaps_in_completeness(sample_repo: Path):
    eng = _engine(sample_repo)
    eng.index(full=True)
    (sample_repo / "pricing" / "rules.py").unlink()
    eng.index(full=False)
    comp = eng.impact("PricingCalculator.calculate")["completeness"]
    assert "gaps" in comp
    assert any(g["name"] == "pricing.rules" for g in comp["gaps"])
    assert comp["level"] in ("partial", "heuristic")
    eng.close()


def test_cli_gaps(capsys, sample_repo: Path):
    (sample_repo / "pricing" / "rules.py").unlink()
    rc = main(["gaps", "--json", "--path", str(sample_repo)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert any(g["name"] == "pricing.rules" for g in data["gaps"])


def test_mcp_list_index_gaps(sample_repo: Path):
    (sample_repo / "pricing" / "rules.py").unlink()
    eng = _engine(sample_repo)
    eng.index(full=True)
    srv = MCPServer(eng)
    tools = {t["name"] for t in srv.handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert "list_index_gaps" in tools
    resp = srv.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "list_index_gaps", "arguments": {}}})
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] and any(g["name"] == "pricing.rules" for g in payload["gaps"])
    eng.close()


def test_gaps_page_renders():
    from uci.api.views import gaps_page
    html = gaps_page({"gaps": [{
        "name": "pricing.rules", "artifact_kind": "module", "ref_count": 2,
        "expected_origin": "pricing/rules", "referencing_sites": [{"path": "a.py", "line": 3}],
    }]})
    assert "Index gaps" in html and "pricing.rules" in html
    assert "No gaps" in gaps_page({"gaps": []})


# --- Phase 4 (recommendations §12) ---------------------------------------------

def test_unindexed_internal_base_class_creates_gap(sample_repo: Path):
    """Finding 12.2: a base class from an unindexed internal module becomes a gap + non-exact impact."""
    (sample_repo / "pricing" / "rules.py").unlink()
    eng = _engine(sample_repo)
    eng.index(full=True)
    gaps = {(g["artifact_kind"], g["name"]) for g in eng.gaps()["gaps"]}
    assert ("class", "pricing.rules.BaseCalculator") in gaps
    imp = eng.impact("PricingCalculator")
    assert imp["completeness"]["level"] != "exact"
    assert any(g["name"] == "pricing.rules.BaseCalculator" for g in imp["completeness"].get("gaps", []))
    eng.close()


def test_stubs_excluded_from_search_and_labeled_in_graph(sample_repo: Path):
    """Finding 12.4: no unlabeled stub reaches an agent surface; graph nodes carry the missing flag."""
    (sample_repo / "pricing" / "rules.py").unlink()
    eng = _engine(sample_repo)
    eng.index(full=True)
    # search and find_symbol never return a placeholder as an ordinary hit
    assert all(not h["missing"] for h in eng.search("rules pricing")["results"])
    assert all(not h["missing"] for h in eng.find_symbol("pricing.rules", exact=False)["results"])
    # the stub exists in the graph, labeled missing (dashed in the dashboard / labeled for agents)
    gap = next(g for g in eng.gaps()["gaps"] if g["artifact_kind"] == "module" and g["name"] == "pricing.rules")
    nb = eng.graph_neighborhood(gap["stub_entity_id"], depth=1)
    root_node = next(n for n in nb["nodes"] if n["id"] == gap["stub_entity_id"])
    assert root_node["missing"] is True
    eng.close()
