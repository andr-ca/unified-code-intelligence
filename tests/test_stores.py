"""VectorStore and MetadataStore contract tests (SQLite backend; ready for more backends)."""

from __future__ import annotations

import pytest

from uci.store.sqlite_backend import SqliteDatabase, SQLiteMetadataStore, SQLiteVectorStore


@pytest.fixture
def db():
    database = SqliteDatabase(":memory:")
    yield database
    database.close()


# --------------------------------------------------------------------------- vector store
@pytest.fixture
def vectors(db):
    return SQLiteVectorStore(db)


def test_vector_upsert_search_orders_by_similarity(vectors):
    vectors.upsert([
        ("c1", [1.0, 0.0, 0.0], {"repo_id": "r", "model": "m"}),
        ("c2", [0.0, 1.0, 0.0], {"repo_id": "r", "model": "m"}),
        ("c3", [0.9, 0.1, 0.0], {"repo_id": "r", "model": "m"}),
    ])
    results = vectors.search([1.0, 0.0, 0.0], top_k=2)
    assert results[0][0] == "c1"
    assert {cid for cid, _ in results} == {"c1", "c3"}


def test_vector_filter_by_repo(vectors):
    vectors.upsert([("a", [1.0, 0.0], {"repo_id": "r1"}), ("b", [1.0, 0.0], {"repo_id": "r2"})])
    results = vectors.search([1.0, 0.0], top_k=10, where={"repo_id": "r1"})
    assert [cid for cid, _ in results] == ["a"]


def test_vector_delete_and_clear(vectors):
    vectors.upsert([("a", [1.0], {"repo_id": "r"}), ("b", [1.0], {"repo_id": "r"})])
    assert vectors.count() == 2
    vectors.delete(["a"])
    assert vectors.count() == 1
    vectors.clear("r")
    assert vectors.count() == 0


# --------------------------------------------------------------------------- metadata store
@pytest.fixture
def meta(db):
    return SQLiteMetadataStore(db)


def test_repository_crud(meta):
    meta.upsert_repository("r1", "Repo", "/root", {"created_at": "now"})
    got = meta.get_repository("r1")
    assert got["name"] == "Repo" and got["root"] == "/root"
    assert [r["repo_id"] for r in meta.list_repositories()] == ["r1"]


def test_file_crud(meta):
    meta.upsert_file("r", "a.py", {"language": "python", "size": 10, "mtime": 1.0, "content_hash": "h"})
    got = meta.get_file("r", "a.py")
    assert got["content_hash"] == "h"
    assert len(meta.list_files("r")) == 1
    meta.delete_file("r", "a.py")
    assert meta.get_file("r", "a.py") is None


def test_chunk_crud(meta):
    meta.upsert_chunk({"id": "ch1", "repo_id": "r", "path": "a.py", "text": "hello", "tokens": ["hello"]})
    assert meta.get_chunk("ch1")["text"] == "hello"
    assert len(list(meta.iter_chunks("r"))) == 1
    meta.delete_chunks_for_file("r", "a.py")
    assert list(meta.iter_chunks("r")) == []


def test_state_roundtrip(meta):
    meta.set_state("r", "key", {"a": 1})
    assert meta.get_state("r", "key") == {"a": 1}
    assert meta.get_state("r", "missing", "default") == "default"


def test_git_commit_and_churn(meta):
    meta.upsert_git_commit("r", "sha1", {"author_email": "x@y", "author_name": "X", "ts": "2026", "message": "m"})
    commits = list(meta.iter_git_commits("r"))
    assert commits[0]["sha"] == "sha1"
    meta.upsert_churn("r", "a.py", {"commits": 3, "authors": ["x@y"], "last_ts": "2026"})
    assert meta.get_churn("r", "a.py")["commits"] == 3


def test_clear_scoped(meta):
    meta.upsert_file("r1", "a.py", {"content_hash": "h"})
    meta.upsert_file("r2", "b.py", {"content_hash": "h"})
    meta.clear("r1")
    assert meta.get_file("r1", "a.py") is None
    assert meta.get_file("r2", "b.py") is not None
