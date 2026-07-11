"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from uci import Config, Engine

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"

# --- Reusable doc+code repo builders (Tasks 6, 8–10, 12) ---
COBOL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. COSGN00C.
       PROCEDURE DIVISION.
       MAIN-PARA.
           MOVE 1 TO X.
"""

README = """\
# App

## Signon — COSGN00C

`COSGN00C` handles signon. See [source](cbl/COSGN00C.cbl).
Missing member `CBTRN99C` is documented but absent. COBOL rules IGNOREME.
"""


def _repo(tmp_path: Path) -> Path:
    """Build a tiny doc+cobol repo for testing."""
    (tmp_path / "cbl").mkdir()
    (tmp_path / "cbl" / "COSGN00C.cbl").write_text(COBOL)
    (tmp_path / "README.md").write_text(README)
    return tmp_path


def _doc_repo_engine(tmp_path: Path, overrides: dict[str, Any] | None = None) -> Engine:
    """Engine factory: build tiny doc+cobol repo and return indexed engine."""
    repo = _repo(tmp_path)
    eng = Engine(Config.from_env(repo, overrides))
    eng.index(full=True)
    return eng


# --- Standard fixtures ---
@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A writable copy of the sample repository (isolated per test)."""
    dest = tmp_path / "sample_repo"
    shutil.copytree(FIXTURE, dest)
    return dest


@pytest.fixture
def engine(sample_repo: Path) -> Engine:
    eng = Engine(Config.from_env(sample_repo))
    yield eng
    eng.close()


@pytest.fixture
def indexed_engine(engine: Engine) -> Engine:
    engine.index(full=True)
    return engine


@pytest.fixture
def noop_engine(sample_repo: Path) -> Engine:
    """Engine with embeddings disabled — proves retrieval works without semantic signal."""
    eng = Engine(Config.from_env(sample_repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    yield eng
    eng.close()
