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


def test_partial_uci_env_does_not_inherit_from_cwd_env(tmp_path: Path, monkeypatch):
    # a .uci/.env that sets only the protocol must NOT inherit model/url from the dev's cwd/.env
    # (the repo's .uci/.env is the complete config unit — no partial merge).
    repo = tmp_path / "repo"
    (repo / ".uci").mkdir(parents=True)
    (repo / ".uci" / ".env").write_text("UCI_LLM_PROTOCOL=ollama\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text(
        "UCI_LLM_PROTOCOL=freellm\nUCI_LLM_MODEL=cloud-model-x\n", encoding="utf-8")
    monkeypatch.chdir(work)
    cfg = Config.from_env(repo)
    assert cfg.llm_protocol == "ollama"   # from .uci/.env
    assert cfg.llm_model == ""            # NOT "cloud-model-x" — cwd/.env did not leak in


def test_cwd_env_applies_when_repo_has_no_uci_env(tmp_path: Path, monkeypatch):
    # a repo without its own .uci/.env falls back to the invocation dir's .env (global default)
    repo = tmp_path / "repo"
    repo.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("UCI_LLM_PROTOCOL=openai\n", encoding="utf-8")
    monkeypatch.chdir(work)
    cfg = Config.from_env(repo)
    assert cfg.llm_protocol == "openai"   # cwd/.env is the default for un-configured repos


def test_real_env_var_wins_over_uci_env(tmp_path: Path, monkeypatch):
    # a real exported UCI_* env var wins over the repo's .uci/.env
    repo = tmp_path / "repo"
    (repo / ".uci").mkdir(parents=True)
    (repo / ".uci" / ".env").write_text("UCI_LLM_PROTOCOL=ollama\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCI_LLM_PROTOCOL", "anthropic")
    cfg = Config.from_env(repo)
    assert cfg.llm_protocol == "anthropic"
