"""SQLite-backed persistence: the default local-first store for the whole platform.

A single :class:`SqliteDatabase` (one ``.uci/uci.db`` file) holds repositories, files, chunks,
entities, relationships, vectors, index state, and git metadata. Three store classes present the
:mod:`uci.core.interfaces` views over that database and can share one connection.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

from ..core.entities import Entity, EntityType
from ..core.interfaces import GraphStore, MetadataStore, VectorStore
from ..core.provenance import Provenance
from ..core.relationships import Relationship, RelationType

try:  # optional acceleration only
    import numpy as _np
except Exception:  # pragma: no cover - numpy is optional
    _np = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
    repo_id TEXT PRIMARY KEY, name TEXT, root TEXT, meta TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS files (
    repo_id TEXT, path TEXT, language TEXT, size INTEGER, mtime REAL,
    content_hash TEXT, indexed_at TEXT, meta TEXT,
    PRIMARY KEY (repo_id, path)
);
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY, repo_id TEXT, kind TEXT, name TEXT, qualified_name TEXT,
    path TEXT, start_line INTEGER, end_line INTEGER, extractor TEXT,
    confidence REAL, attributes TEXT
);
CREATE INDEX IF NOT EXISTS idx_entities_repo_kind ON entities(repo_id, kind);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_entities_qname ON entities(qualified_name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_entities_path ON entities(repo_id, path);
CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY, repo_id TEXT, type TEXT, src_id TEXT, dst_id TEXT,
    path TEXT, start_line INTEGER, end_line INTEGER, extractor TEXT,
    confidence REAL, attributes TEXT
);
CREATE INDEX IF NOT EXISTS idx_rel_src ON relationships(src_id, type);
CREATE INDEX IF NOT EXISTS idx_rel_dst ON relationships(dst_id, type);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(type);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, repo_id TEXT, path TEXT, symbol TEXT, kind TEXT,
    language TEXT, start_line INTEGER, end_line INTEGER, text TEXT,
    entity_id TEXT, tokens TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo_id);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_chunks_entity ON chunks(entity_id);
CREATE TABLE IF NOT EXISTS vectors (
    chunk_id TEXT PRIMARY KEY, repo_id TEXT, dim INTEGER, vector TEXT, model TEXT
);
CREATE INDEX IF NOT EXISTS idx_vectors_repo ON vectors(repo_id);
CREATE TABLE IF NOT EXISTS state (
    repo_id TEXT, key TEXT, value TEXT, PRIMARY KEY (repo_id, key)
);
CREATE TABLE IF NOT EXISTS git_commits (
    repo_id TEXT, sha TEXT, author_email TEXT, author_name TEXT, ts TEXT,
    message TEXT, PRIMARY KEY (repo_id, sha)
);
CREATE TABLE IF NOT EXISTS git_churn (
    repo_id TEXT, path TEXT, commits INTEGER, last_ts TEXT, authors TEXT,
    PRIMARY KEY (repo_id, path)
);
CREATE TABLE IF NOT EXISTS gaps (
    repo_id TEXT, artifact_kind TEXT, name TEXT, stub_entity_id TEXT, expected_origin TEXT,
    reasons TEXT, ref_count INTEGER, referencing_sites TEXT, first_seen TEXT,
    last_seen_generation INTEGER, confidence REAL,
    PRIMARY KEY (repo_id, artifact_kind, name)
);
CREATE INDEX IF NOT EXISTS idx_gaps_repo ON gaps(repo_id);
"""


class SqliteDatabase:
    """Owns the SQLite connection and schema. Thread-safe for the simple write patterns UCI uses."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        if self.path != ":memory:":
            try:
                self.conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:  # pragma: no cover
                pass
        self.conn.executescript(_SCHEMA)
        # FTS5 lexical index over chunk text (the keyword signal). Optional: some SQLite builds
        # lack the fts5 extension — retrieval falls back to the token-overlap scan.
        self.has_fts = False
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING "
                "fts5(text, chunk_id UNINDEXED, repo_id UNINDEXED)"
            )
            self.has_fts = True
        except sqlite3.OperationalError:  # pragma: no cover - build-dependent
            pass
        self.conn.commit()

    def commit(self) -> None:
        with self._lock:
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.commit()
            self.conn.close()

    def __enter__(self) -> SqliteDatabase:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _row_to_entity(row: sqlite3.Row) -> Entity:
    prov = Provenance(
        repo_id=row["repo_id"] or "",
        path=row["path"] or "",
        start_line=row["start_line"] or 0,
        end_line=row["end_line"] or 0,
        extractor=row["extractor"] or "unknown",
        confidence=row["confidence"] if row["confidence"] is not None else 1.0,
    )
    return Entity(
        id=row["id"],
        kind=EntityType(row["kind"]),
        name=row["name"],
        qualified_name=row["qualified_name"] or row["name"],
        provenance=prov,
        attributes=json.loads(row["attributes"] or "{}"),
    )


def _row_to_rel(row: sqlite3.Row) -> Relationship:
    prov = Provenance(
        repo_id=row["repo_id"] or "",
        path=row["path"] or "",
        start_line=row["start_line"] or 0,
        end_line=row["end_line"] or 0,
        extractor=row["extractor"] or "unknown",
        confidence=row["confidence"] if row["confidence"] is not None else 1.0,
    )
    return Relationship(
        id=row["id"],
        type=RelationType(row["type"]),
        src_id=row["src_id"],
        dst_id=row["dst_id"],
        provenance=prov,
        attributes=json.loads(row["attributes"] or "{}"),
    )


# ---------------------------------------------------------------------------- graph store
class SQLiteGraphStore(GraphStore):
    def __init__(self, db: SqliteDatabase) -> None:
        self.db = db

    def _entity_row(self, e: Entity) -> tuple:
        p = e.provenance
        return (
            e.id, p.repo_id, e.kind.value, e.name, e.qualified_name, p.path,
            p.start_line, p.end_line, p.extractor, p.confidence, _dumps(e.attributes),
        )

    def _rel_row(self, r: Relationship) -> tuple:
        p = r.provenance
        return (
            r.id, p.repo_id, r.type.value, r.src_id, r.dst_id, p.path,
            p.start_line, p.end_line, p.extractor, p.confidence, _dumps(r.attributes),
        )

    def add_entity(self, entity: Entity) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            self._entity_row(entity),
        )
        self.db.commit()

    def add_entities(self, entities: Iterable[Entity]) -> None:
        rows = [self._entity_row(e) for e in entities]
        if rows:
            self.db.conn.executemany(
                "INSERT OR REPLACE INTO entities VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
            )
            self.db.commit()

    def add_relationship(self, rel: Relationship) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            self._rel_row(rel),
        )
        self.db.commit()

    def add_relationships(self, rels: Iterable[Relationship]) -> None:
        rows = [self._rel_row(r) for r in rels]
        if rows:
            self.db.conn.executemany(
                "INSERT OR REPLACE INTO relationships VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
            )
            self.db.commit()

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.db.conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        return _row_to_entity(row) if row else None

    def entities(
        self, kind: EntityType | None = None, repo_id: str | None = None
    ) -> Iterator[Entity]:
        sql = "SELECT * FROM entities"
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind.value)
        if repo_id is not None:
            clauses.append("repo_id=?")
            params.append(repo_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        for row in self.db.conn.execute(sql, params):
            yield _row_to_entity(row)

    def relationships(self, rtype: RelationType | None = None) -> Iterator[Relationship]:
        if rtype is None:
            cur = self.db.conn.execute("SELECT * FROM relationships")
        else:
            cur = self.db.conn.execute("SELECT * FROM relationships WHERE type=?", (rtype.value,))
        for row in cur:
            yield _row_to_rel(row)

    def _rel_where(self, column: str, entity_id: str, rtypes) -> list[Relationship]:
        sql = f"SELECT * FROM relationships WHERE {column}=?"
        params: list[Any] = [entity_id]
        rtypes = list(rtypes) if rtypes is not None else None
        if rtypes:
            placeholders = ",".join("?" for _ in rtypes)
            sql += f" AND type IN ({placeholders})"
            params.extend(rt.value for rt in rtypes)
        return [_row_to_rel(r) for r in self.db.conn.execute(sql, params)]

    def out_relationships(self, entity_id: str, rtypes=None) -> list[Relationship]:
        return self._rel_where("src_id", entity_id, rtypes)

    def in_relationships(self, entity_id: str, rtypes=None) -> list[Relationship]:
        return self._rel_where("dst_id", entity_id, rtypes)

    def find_by_name(
        self, name: str, kind: EntityType | None = None, exact: bool = True
    ) -> list[Entity]:
        params: list[Any] = []
        if exact:
            where = "(name=? COLLATE NOCASE OR qualified_name=? COLLATE NOCASE)"
            params.extend([name, name])
        else:
            where = "(name LIKE ? COLLATE NOCASE OR qualified_name LIKE ? COLLATE NOCASE)"
            like = f"%{name}%"
            params.extend([like, like])
        if kind is not None:
            where += " AND kind=?"
            params.append(kind.value)
        rows = self.db.conn.execute(f"SELECT * FROM entities WHERE {where}", params)
        return [_row_to_entity(r) for r in rows]

    def clear(self, repo_id: str | None = None) -> None:
        if repo_id is None:
            self.db.conn.execute("DELETE FROM entities")
            self.db.conn.execute("DELETE FROM relationships")
        else:
            self.db.conn.execute("DELETE FROM entities WHERE repo_id=?", (repo_id,))
            self.db.conn.execute("DELETE FROM relationships WHERE repo_id=?", (repo_id,))
        self.db.commit()


# ---------------------------------------------------------------------------- vector store
def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


class SQLiteVectorStore(VectorStore):
    """Brute-force cosine search. Fast enough for local repos; numpy-accelerated when available."""

    def __init__(self, db: SqliteDatabase) -> None:
        self.db = db

    def upsert(self, items: Sequence[tuple[str, Sequence[float], dict[str, Any]]]) -> None:
        rows = [
            (chunk_id, meta.get("repo_id", ""), len(vec), _dumps(list(vec)), meta.get("model", ""))
            for chunk_id, vec, meta in items
        ]
        if rows:
            self.db.conn.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)", rows)
            self.db.commit()

    def search(
        self, vector: Sequence[float], top_k: int = 10, where: dict[str, Any] | None = None
    ) -> list[tuple[str, float]]:
        sql = "SELECT chunk_id, vector FROM vectors"
        params: list[Any] = []
        if where and where.get("repo_id"):
            sql += " WHERE repo_id=?"
            params.append(where["repo_id"])
        rows = self.db.conn.execute(sql, params).fetchall()
        if not rows:
            return []
        query = list(vector)
        if _np is not None:
            mat = _np.array([json.loads(r["vector"]) for r in rows], dtype="float32")
            q = _np.array(query, dtype="float32")
            denom = (_np.linalg.norm(mat, axis=1) * (_np.linalg.norm(q) or 1.0))
            denom[denom == 0] = 1.0
            sims = (mat @ q) / denom
            scored = list(zip((r["chunk_id"] for r in rows), (float(s) for s in sims)))
        else:
            scored = [(r["chunk_id"], _cosine(query, json.loads(r["vector"]))) for r in rows]
        scored.sort(key=lambda kv: kv[1], reverse=True)
        return scored[:top_k]

    def delete(self, chunk_ids: Iterable[str]) -> None:
        ids = list(chunk_ids)
        if ids:
            self.db.conn.executemany("DELETE FROM vectors WHERE chunk_id=?", [(i,) for i in ids])
            self.db.commit()

    def clear(self, repo_id: str | None = None) -> None:
        if repo_id is None:
            self.db.conn.execute("DELETE FROM vectors")
        else:
            self.db.conn.execute("DELETE FROM vectors WHERE repo_id=?", (repo_id,))
        self.db.commit()

    def count(self) -> int:
        return int(self.db.conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0])


# ---------------------------------------------------------------------------- metadata store
class SQLiteMetadataStore(MetadataStore):
    def __init__(self, db: SqliteDatabase) -> None:
        self.db = db

    # repositories
    def upsert_repository(self, repo_id: str, name: str, root: str, meta: dict[str, Any]) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO repositories VALUES (?,?,?,?,?)",
            (repo_id, name, root, _dumps(meta), meta.get("created_at", "")),
        )
        self.db.commit()

    def get_repository(self, repo_id: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT * FROM repositories WHERE repo_id=?", (repo_id,)
        ).fetchone()
        if not row:
            return None
        return {"repo_id": row["repo_id"], "name": row["name"], "root": row["root"],
                "meta": json.loads(row["meta"] or "{}")}

    def list_repositories(self) -> list[dict[str, Any]]:
        rows = self.db.conn.execute("SELECT * FROM repositories").fetchall()
        return [{"repo_id": r["repo_id"], "name": r["name"], "root": r["root"],
                 "meta": json.loads(r["meta"] or "{}")} for r in rows]

    # files
    def upsert_file(self, repo_id: str, path: str, record: dict[str, Any]) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?)",
            (
                repo_id, path, record.get("language", ""), record.get("size", 0),
                record.get("mtime", 0.0), record.get("content_hash", ""),
                record.get("indexed_at", ""), _dumps(record.get("meta", {})),
            ),
        )
        self.db.commit()

    def get_file(self, repo_id: str, path: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT * FROM files WHERE repo_id=? AND path=?", (repo_id, path)
        ).fetchone()
        if not row:
            return None
        return {
            "repo_id": row["repo_id"], "path": row["path"], "language": row["language"],
            "size": row["size"], "mtime": row["mtime"], "content_hash": row["content_hash"],
            "indexed_at": row["indexed_at"], "meta": json.loads(row["meta"] or "{}"),
        }

    def list_files(self, repo_id: str) -> list[dict[str, Any]]:
        rows = self.db.conn.execute("SELECT * FROM files WHERE repo_id=?", (repo_id,)).fetchall()
        return [
            {"repo_id": r["repo_id"], "path": r["path"], "language": r["language"],
             "size": r["size"], "mtime": r["mtime"], "content_hash": r["content_hash"],
             "indexed_at": r["indexed_at"], "meta": json.loads(r["meta"] or "{}")}
            for r in rows
        ]

    def delete_file(self, repo_id: str, path: str) -> None:
        self.db.conn.execute("DELETE FROM files WHERE repo_id=? AND path=?", (repo_id, path))
        self.db.commit()

    # chunks
    def upsert_chunk(self, chunk: dict[str, Any]) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                chunk["id"], chunk.get("repo_id", ""), chunk.get("path", ""),
                chunk.get("symbol", ""), chunk.get("kind", ""), chunk.get("language", ""),
                chunk.get("start_line", 0), chunk.get("end_line", 0), chunk.get("text", ""),
                chunk.get("entity_id", ""), _dumps(chunk.get("tokens", [])),
            ),
        )
        self._fts_replace([chunk])
        self.db.commit()

    def upsert_chunks(self, chunks: Sequence[dict[str, Any]]) -> None:
        rows = [
            (
                c["id"], c.get("repo_id", ""), c.get("path", ""), c.get("symbol", ""),
                c.get("kind", ""), c.get("language", ""), c.get("start_line", 0),
                c.get("end_line", 0), c.get("text", ""), c.get("entity_id", ""),
                _dumps(c.get("tokens", [])),
            )
            for c in chunks
        ]
        if rows:
            self.db.conn.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._fts_replace(chunks)
            self.db.commit()

    # -- FTS5 lexical index ---------------------------------------------------
    def _fts_replace(self, chunks: Sequence[dict[str, Any]]) -> None:
        if not self.db.has_fts:
            return
        self.db.conn.executemany(
            "DELETE FROM chunk_fts WHERE chunk_id=?", [(c["id"],) for c in chunks]
        )
        self.db.conn.executemany(
            "INSERT INTO chunk_fts (text, chunk_id, repo_id) VALUES (?,?,?)",
            [(f"{c.get('symbol', '')} {c.get('text', '')}", c["id"], c.get("repo_id", ""))
             for c in chunks],
        )

    def search_text(self, repo_id: str, query: str, limit: int = 30) -> list[tuple[str, float]] | None:
        """BM25-ranked chunk search. Returns None when FTS5 is unavailable (caller falls back)."""
        if not self.db.has_fts:
            return None
        tokens = [t for t in re.findall(r"[A-Za-z0-9_]+", query) if len(t) > 1][:12]
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens)
        try:
            rows = self.db.conn.execute(
                "SELECT chunk_id, bm25(chunk_fts) AS score FROM chunk_fts "
                "WHERE chunk_fts MATCH ? AND repo_id=? ORDER BY score LIMIT ?",
                (match, repo_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:  # pragma: no cover - malformed query safety
            return []
        # bm25 returns lower-is-better; normalize to higher-is-better
        return [(r["chunk_id"], -float(r["score"])) for r in rows]

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        row = self.db.conn.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return _chunk_row(row) if row else None

    def iter_chunks(self, repo_id: str | None = None) -> Iterator[dict[str, Any]]:
        if repo_id is None:
            cur = self.db.conn.execute("SELECT * FROM chunks")
        else:
            cur = self.db.conn.execute("SELECT * FROM chunks WHERE repo_id=?", (repo_id,))
        for row in cur:
            yield _chunk_row(row)

    def delete_chunks_for_file(self, repo_id: str, path: str) -> None:
        if self.db.has_fts:
            self.db.conn.execute(
                "DELETE FROM chunk_fts WHERE chunk_id IN "
                "(SELECT id FROM chunks WHERE repo_id=? AND path=?)", (repo_id, path)
            )
        self.db.conn.execute("DELETE FROM chunks WHERE repo_id=? AND path=?", (repo_id, path))
        self.db.commit()

    # state
    def set_state(self, repo_id: str, key: str, value: Any) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO state VALUES (?,?,?)", (repo_id, key, _dumps(value))
        )
        self.db.commit()

    def get_state(self, repo_id: str, key: str, default: Any = None) -> Any:
        row = self.db.conn.execute(
            "SELECT value FROM state WHERE repo_id=? AND key=?", (repo_id, key)
        ).fetchone()
        return json.loads(row["value"]) if row else default

    def clear(self, repo_id: str | None = None) -> None:
        tables = ["files", "chunks", "state", "gaps"]
        if self.db.has_fts:
            tables.append("chunk_fts")
        for table in tables:
            if repo_id is None:
                self.db.conn.execute(f"DELETE FROM {table}")
            else:
                self.db.conn.execute(f"DELETE FROM {table} WHERE repo_id=?", (repo_id,))
        self.db.commit()

    # git
    def upsert_git_commit(self, repo_id: str, sha: str, record: dict[str, Any]) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO git_commits VALUES (?,?,?,?,?,?)",
            (repo_id, sha, record.get("author_email", ""), record.get("author_name", ""),
             record.get("ts", ""), record.get("message", "")),
        )
        self.db.commit()

    def iter_git_commits(self, repo_id: str) -> Iterator[dict[str, Any]]:
        cur = self.db.conn.execute(
            "SELECT * FROM git_commits WHERE repo_id=? ORDER BY ts DESC", (repo_id,)
        )
        for r in cur:
            yield {"sha": r["sha"], "author_email": r["author_email"],
                   "author_name": r["author_name"], "ts": r["ts"], "message": r["message"]}

    def upsert_churn(self, repo_id: str, path: str, record: dict[str, Any]) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO git_churn VALUES (?,?,?,?,?)",
            (repo_id, path, record.get("commits", 0), record.get("last_ts", ""),
             _dumps(record.get("authors", []))),
        )
        self.db.commit()

    def get_churn(self, repo_id: str, path: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT * FROM git_churn WHERE repo_id=? AND path=?", (repo_id, path)
        ).fetchone()
        if not row:
            return None
        return {"commits": row["commits"], "last_ts": row["last_ts"],
                "authors": json.loads(row["authors"] or "[]")}

    # gap registry
    def upsert_gap(self, repo_id: str, gap: dict[str, Any]) -> None:
        self.db.conn.execute(
            "INSERT OR REPLACE INTO gaps VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                repo_id, gap.get("artifact_kind", ""), gap.get("name", ""),
                gap.get("stub_entity_id", ""), gap.get("expected_origin", ""),
                _dumps(gap.get("reasons", [])), gap.get("ref_count", 0),
                _dumps(gap.get("referencing_sites", [])), gap.get("first_seen", ""),
                gap.get("last_seen_generation", 0), gap.get("confidence", 1.0),
            ),
        )
        self.db.commit()

    def iter_gaps(self, repo_id: str) -> Iterator[dict[str, Any]]:
        cur = self.db.conn.execute(
            "SELECT * FROM gaps WHERE repo_id=? ORDER BY ref_count DESC", (repo_id,)
        )
        for r in cur:
            yield {
                "artifact_kind": r["artifact_kind"], "name": r["name"],
                "stub_entity_id": r["stub_entity_id"], "expected_origin": r["expected_origin"],
                "reasons": json.loads(r["reasons"] or "[]"), "ref_count": r["ref_count"],
                "referencing_sites": json.loads(r["referencing_sites"] or "[]"),
                "first_seen": r["first_seen"], "last_seen_generation": r["last_seen_generation"],
                "confidence": r["confidence"],
            }

    def clear_gaps(self, repo_id: str) -> None:
        self.db.conn.execute("DELETE FROM gaps WHERE repo_id=?", (repo_id,))
        self.db.commit()


def _chunk_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "repo_id": row["repo_id"], "path": row["path"],
        "symbol": row["symbol"], "kind": row["kind"], "language": row["language"],
        "start_line": row["start_line"], "end_line": row["end_line"], "text": row["text"],
        "entity_id": row["entity_id"], "tokens": json.loads(row["tokens"] or "[]"),
    }


__all__ = [
    "SqliteDatabase",
    "SQLiteGraphStore",
    "SQLiteVectorStore",
    "SQLiteMetadataStore",
]
