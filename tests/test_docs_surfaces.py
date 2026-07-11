"""Documentation surfaces: impact pack + entity detail expose describing doc sections (risk-neutral)."""

from __future__ import annotations

from pathlib import Path

from uci import Config, Engine

COBOL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. COSGN00C.
       PROCEDURE DIVISION.
           MOVE 1 TO X.
"""

README = """\
# App

## Signon — COSGN00C

`COSGN00C` handles signon. See [source](cbl/COSGN00C.cbl).
"""


def _mk(dirp: Path, with_readme: bool) -> Engine:
    dirp.mkdir(parents=True, exist_ok=True)
    (dirp / "cbl").mkdir()
    (dirp / "cbl" / "COSGN00C.cbl").write_text(COBOL)
    if with_readme:
        (dirp / "README.md").write_text(README)
    eng = Engine(Config.from_env(dirp))
    eng.index(full=True)
    return eng


def test_impact_pack_includes_documentation(tmp_path):
    with _mk(tmp_path / "withdocs", True) as eng, _mk(tmp_path / "nodocs", False) as eng2:
        pack = eng.impact("COSGN00C")
        docs = pack["documentation"]
        assert docs and docs[0]["heading"].startswith("Signon")
        assert docs[0]["path"] == "README.md" and docs[0]["resolution"] == "doc-heading"
        # risk unchanged by docs: compare against a docless twin
        assert pack["risk"] == eng2.impact("COSGN00C")["risk"]


def test_entity_detail_lists_documentation(tmp_path):
    with _mk(tmp_path / "detail", True) as eng:
        sym = eng.find_symbol("COSGN00C")["results"][0]
        detail = eng.entity_detail(sym["entity_id"])
        assert any(d["path"] == "README.md" for d in detail.get("documentation", []))


def test_docs_overview_and_page_render(tmp_path):
    from uci.api import views

    with _mk(tmp_path / "dash", True) as eng:
        data = eng.docs_overview()
        assert data["coverage"]["total"] >= 1
        assert any(d["path"] == "README.md" for d in data["documents"])
        html = views.docs_page(data)
        assert "Documentation" in html and "README.md" in html
        detail = eng.doc_detail("README.md")
        assert detail["ok"] and detail["sections"]
        assert "Signon" in views.doc_detail_page(detail)
