"""Config loads the repo `.env` from its `.uci/` store dir, never from the repo root."""

from __future__ import annotations

from pathlib import Path

import pytest

from uci import Config


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    # isolate from any UCI_* vars in the ambient environment
    for var in ("UCI_LLM_PROTOCOL", "UCI_LLM_MODEL", "UCI_LLM_URL"):
        monkeypatch.delenv(var, raising=False)


def test_env_loaded_from_uci_store_dir(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".uci").mkdir(parents=True)
    (repo / ".uci" / ".env").write_text(
        "UCI_LLM_PROTOCOL=freellm\nUCI_LLM_MODEL=picked\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # keep cwd/.env out of the picture
    cfg = Config.from_env(repo)
    assert cfg.llm_protocol == "freellm"
    assert cfg.llm_model == "picked"


def test_root_env_is_ignored(tmp_path: Path, monkeypatch):
    # a .env at the processed repo's ROOT must NOT be read — config belongs in .uci/
    repo = tmp_path / "repo"
    (repo / ".uci").mkdir(parents=True)
    (repo / ".env").write_text("UCI_LLM_PROTOCOL=anthropic\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    cfg = Config.from_env(repo)
    assert cfg.llm_protocol == "ollama"  # default; the root .env was ignored


def test_uci_env_overrides_cwd_env(tmp_path: Path, monkeypatch):
    # the repo's .uci/.env is authoritative over the invocation dir's .env
    repo = tmp_path / "repo"
    (repo / ".uci").mkdir(parents=True)
    (repo / ".uci" / ".env").write_text("UCI_LLM_PROTOCOL=freellm\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("UCI_LLM_PROTOCOL=openai\n", encoding="utf-8")
    monkeypatch.chdir(work)
    cfg = Config.from_env(repo)
    assert cfg.llm_protocol == "freellm"  # .uci/.env wins over cwd/.env
