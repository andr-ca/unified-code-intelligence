"""Shared pytest fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from uci import Config, Engine

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


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
