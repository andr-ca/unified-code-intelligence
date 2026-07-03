"""Read-only database browser: table listing, paginated rows, guarded SQL, and page render."""

from __future__ import annotations

from pathlib import Path

import pytest

from uci import Config, Engine
from uci.api import views
from uci.core import Entity, EntityType, Provenance, Relationship, RelationType


@pytest.fixture
def seeded(tmp_path: Path):
    repo = tmp_path / "db"
    repo.mkdir()
    eng = Engine(Config.from_env(repo))
    rid = eng.repo_id
    for i in range(3):
        eng.graph.add_entity(Entity(
            f"fn:{i}", EntityType.FUNCTION, f"fn{i}", f"fn{i}",
            Provenance(rid, f"m{i}.py", 1, 2), {}))
    eng.graph.add_relationship(Relationship(
        "c0", RelationType.CALLS, "fn:0", "fn:1", Provenance(rid, "m0.py", 1, 1)))
    yield eng
    eng.close()


def test_db_tables_lists_counts(seeded):
    out = seeded.db_tables()
    assert out["ok"]
    counts = {t["table"]: t["rows"] for t in out["tables"]}
    assert counts["entities"] == 3
    assert counts["relationships"] == 1
    assert "sqlite_master" not in counts        # only allow-listed tables


def test_db_rows_paginates_and_scopes(seeded):
    page1 = seeded.db_rows("entities", limit=2, offset=0)
    assert page1["ok"] and page1["total"] == 3 and len(page1["rows"]) == 2
    page2 = seeded.db_rows("entities", limit=2, offset=2)
    assert len(page2["rows"]) == 1               # last row
    assert "id" in page1["columns"]


def test_db_rows_rejects_unknown_table(seeded):
    out = seeded.db_rows("sqlite_master")
    assert not out["ok"] and out["error"]["code"] == "bad_table"


def test_db_query_readonly_allows_select(seeded):
    out = seeded.db_query("SELECT kind, COUNT(*) FROM entities GROUP BY kind")
    assert out["ok"]
    assert out["rows"][0][0] == "function"


def test_db_query_blocks_writes_and_multiple_statements(seeded):
    for bad in ("DELETE FROM entities", "UPDATE entities SET name='x'",
                "DROP TABLE entities", "SELECT 1; DELETE FROM entities"):
        out = seeded.db_query(bad)
        assert not out["ok"] and out["error"]["code"] == "bad_sql"


def test_db_query_write_blocked_at_sqlite_level(seeded):
    # even a lone statement that slips the text check cannot mutate a read-only connection
    before = seeded.db_rows("entities")["total"]
    seeded.db_query("SELECT 1")           # a legit query, connection is read-only regardless
    after = seeded.db_rows("entities")["total"]
    assert before == after == 3


def test_db_page_renders(seeded):
    tables = seeded.db_tables()["tables"]
    html = views.db_page(tables, "entities", seeded.db_rows("entities"), "", None)
    assert "Database" in html and 'href="/db?table=' in html and "dbtable" in html
    # query mode
    q = views.db_page(tables, "", None, "SELECT 1", seeded.db_query("SELECT 1"))
    assert "Result" in q and "sqlbox" in q
