"""CLI tests: exercise the argparse entry point end-to-end on the sample repo."""

from __future__ import annotations

import json
from pathlib import Path

from uci.cli.main import main


def test_cli_index(capsys, sample_repo: Path):
    rc = main(["index", str(sample_repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Indexed" in out


def test_cli_index_json(capsys, sample_repo: Path):
    rc = main(["index", str(sample_repo), "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["entities"] > 0


def test_cli_query(capsys, sample_repo: Path):
    main(["index", str(sample_repo)])
    capsys.readouterr()
    rc = main(["query", "calculate", "--path", str(sample_repo)])
    assert rc == 0
    assert "Query:" in capsys.readouterr().out


def test_cli_impact(capsys, sample_repo: Path):
    main(["index", str(sample_repo)])
    capsys.readouterr()
    rc = main(["impact", "PricingCalculator.calculate", "--path", str(sample_repo)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Impact:" in out and "Risk:" in out


def test_cli_overview_json(capsys, sample_repo: Path):
    rc = main(["overview", "--json", "--path", str(sample_repo)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["totals"]["classes"] >= 3


def test_cli_graph_symbol(capsys, sample_repo: Path):
    main(["index", str(sample_repo)])
    capsys.readouterr()
    rc = main(["graph", "symbol", "PricingCalculator", "--path", str(sample_repo)])
    assert rc == 0
    assert "PricingCalculator" in capsys.readouterr().out


def test_cli_init(capsys, sample_repo: Path):
    rc = main(["init", str(sample_repo)])
    assert rc == 0
    assert (sample_repo / ".uci" / "config.json").exists()


def test_cli_docs_coverage(capsys, tmp_path: Path):
    (tmp_path / "cbl").mkdir()
    (tmp_path / "cbl" / "COSGN00C.cbl").write_text(
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. COSGN00C.\n"
        "       PROCEDURE DIVISION.\n           MOVE 1 TO X.\n")
    (tmp_path / "README.md").write_text(
        "# App\n\n## Signon — COSGN00C\n\n`COSGN00C` handles signon.\n")
    rc = main(["docs", "--path", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "coverage" in out.lower() and "README.md" in out
