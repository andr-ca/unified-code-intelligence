"""Declarative configuration and backend selection.

Everything is driven by a single immutable :class:`Config`. Backends are chosen by name (strings)
so no core code imports a vendor SDK. Values come from (in order of precedence):

1. explicit ``overrides`` passed in code / CLI flags,
2. ``UCI_*`` environment variables,
3. a ``.env`` file in the repo's ``.uci/`` store dir or the current directory,
4. profile defaults (``local-lite`` / ``local-pro`` / ``cloud``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_IGNORE_GLOBS: tuple[str, ...] = (
    ".git", ".hg", ".svn", ".uci", ".coderag", ".understand-anything",
    "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "dist", "build", "target", ".next", ".nuxt", "out", "coverage",
    ".idea", ".vscode", "*.min.js", "*.lock", "*.png", "*.jpg", "*.jpeg", "*.gif",
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.so", "*.dylib", "*.dll", "*.class",
    "*.pyc", "*.o", "*.a", "*.bin", "*.wasm",
)

# Profile -> backend defaults. local-lite requires nothing external.
_PROFILES: dict[str, dict[str, str]] = {
    "local-lite": {
        "graph_backend": "sqlite",
        "vector_backend": "sqlite",
        "metadata_backend": "sqlite",
        "embedding_provider": "local",
    },
    "local-pro": {
        "graph_backend": "memgraph",
        "vector_backend": "qdrant",
        "metadata_backend": "sqlite",
        "embedding_provider": "ollama",
    },
    "cloud": {
        "graph_backend": "neo4j",
        "vector_backend": "qdrant",
        "metadata_backend": "postgres",
        "embedding_provider": "openai",
    },
}

_TRUE = {"1", "true", "yes", "on"}

#: Name prefixes classified as external platform artifacts (never "missing" gaps).
_DEFAULT_GAP_PREFIXES = (
    # CICS, DB2, LE, IMS, COBOL runtime, IDMS, DB2 catalog
    "DFH", "DSN", "CEE", "DFS", "IGZ", "IDMS", "SYSIBM",
    # MQ APIs + MQ copybooks (CMQ*), MQ subsystem, SQL artifacts (SQLCA/SQLDA),
    # IMS DL/I (CBLTDLI), PL/I library
    "MQ", "CMQ", "CSQ", "SQL", "CBLTDLI", "ILBO",
)


def _parse_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return data
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            data[key] = value
    return data


@dataclass(frozen=True)
class Config:
    """Immutable configuration object passed explicitly everywhere (no globals)."""

    repo_path: Path = field(default_factory=lambda: Path.cwd())
    profile: str = "local-lite"

    # backends (strings; adapters resolve them lazily)
    graph_backend: str = "sqlite"
    vector_backend: str = "sqlite"
    metadata_backend: str = "sqlite"
    embedding_provider: str = "local"
    embedding_model: str = "hash-64"
    embedding_dim: int = 64

    # ingest
    use_gitignore: bool = True
    ignore_globs: tuple[str, ...] = DEFAULT_IGNORE_GLOBS
    max_file_bytes: int = 1_500_000
    index_all_text: bool = False

    # chunking
    max_chunk_lines: int = 200
    window_lines: int = 60
    window_overlap: int = 10

    # gap registry: name prefixes treated as external (not "missing") — e.g. mainframe system modules
    gap_external_prefixes: tuple[str, ...] = _DEFAULT_GAP_PREFIXES

    # optional LLM enrichment (docs/llm-enrichment.md). Protocol: ollama | openai | anthropic | freellm.
    # The API key lives in settings["llm_api_key"] (never in to_dict()/reports).
    llm_protocol: str = "ollama"
    llm_url: str = ""            # empty -> protocol default (localhost Ollama / api.openai.com / api.anthropic.com)
    llm_model: str = ""          # empty -> protocol default
    llm_timeout: int = 60
    llm_max_tokens: int = 700
    llm_log: str = ""            # LLM call log: ""->.uci/llm-calls.jsonl, "off"->disabled, else a path

    # retrieval fusion weights (graph-first defaults)
    weight_symbol: float = 1.4
    weight_keyword: float = 1.0
    weight_semantic: float = 1.0
    weight_graph: float = 0.8
    weight_proximity: float = 0.4
    weight_churn: float = 0.3
    rrf_k: int = 60

    # optional backend connection settings
    settings: dict[str, Any] = field(default_factory=dict)

    # -- derived paths ------------------------------------------------------
    @property
    def store_dir(self) -> Path:
        return self.repo_path / ".uci"

    @property
    def db_path(self) -> Path:
        return self.store_dir / "uci.db"

    def with_overrides(self, **kwargs: Any) -> Config:
        return replace(self, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": str(self.repo_path),
            "profile": self.profile,
            "graph_backend": self.graph_backend,
            "vector_backend": self.vector_backend,
            "metadata_backend": self.metadata_backend,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
        }

    # -- construction -------------------------------------------------------
    @classmethod
    def from_env(
        cls,
        repo_path: str | os.PathLike[str] | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> Config:
        repo = Path(repo_path or os.environ.get("UCI_REPO_PATH") or Path.cwd()).resolve()

        # Load config env. The repo's own .uci/.env is the COMPLETE config unit — when it exists
        # it is authoritative and the invocation dir's .env is ignored entirely, so a partial
        # .uci/.env never inherits stray keys (protocol/model/url) from the developer's working-dir
        # .env. cwd/.env applies only to a repo that has no .uci/.env of its own. Real UCI_* env
        # vars always win. The processed repo's ROOT .env is never read — config belongs under .uci/.
        env: dict[str, str] = {}
        uci_env = repo / ".uci" / ".env"
        if uci_env.exists():
            env = _parse_env_file(uci_env)
        elif (Path.cwd() / ".env").exists():
            env = _parse_env_file(Path.cwd() / ".env")
        env.update({k: v for k, v in os.environ.items() if k.startswith("UCI_")})

        profile = (overrides or {}).get("profile") or env.get("UCI_PROFILE", "local-lite")
        defaults = _PROFILES.get(profile, _PROFILES["local-lite"])

        def pick(field_name: str, env_key: str, fallback: Any) -> Any:
            if overrides and field_name in overrides and overrides[field_name] is not None:
                return overrides[field_name]
            if env_key in env:
                return env[env_key]
            return fallback

        embedding_provider = pick(
            "embedding_provider", "UCI_EMBEDDING_PROVIDER", defaults["embedding_provider"]
        )
        embedding_model, embedding_dim = _default_model_for(embedding_provider, env)

        cfg = cls(
            repo_path=repo,
            profile=profile,
            graph_backend=pick("graph_backend", "UCI_GRAPH_BACKEND", defaults["graph_backend"]),
            vector_backend=pick("vector_backend", "UCI_VECTOR_BACKEND", defaults["vector_backend"]),
            metadata_backend=pick(
                "metadata_backend", "UCI_METADATA_BACKEND", defaults["metadata_backend"]
            ),
            embedding_provider=embedding_provider,
            embedding_model=str(pick("embedding_model", "UCI_EMBEDDING_MODEL", embedding_model)),
            embedding_dim=int(pick("embedding_dim", "UCI_EMBEDDING_DIM", embedding_dim)),
            use_gitignore=str(env.get("UCI_USE_GITIGNORE", "1")).lower() in _TRUE,
            index_all_text=str(env.get("UCI_INDEX_ALL_TEXT", "0")).lower() in _TRUE,
            gap_external_prefixes=_gap_prefixes(env),
            weight_symbol=_num(env, "UCI_WEIGHT_SYMBOL", 1.4),
            weight_keyword=_num(env, "UCI_WEIGHT_KEYWORD", 1.0),
            weight_semantic=_num(env, "UCI_WEIGHT_SEMANTIC", 1.0),
            weight_graph=_num(env, "UCI_WEIGHT_GRAPH", 0.8),
            weight_proximity=_num(env, "UCI_WEIGHT_PROXIMITY", 0.4),
            weight_churn=_num(env, "UCI_WEIGHT_CHURN", 0.3),
            rrf_k=int(_num(env, "UCI_RRF_K", 60)),
            llm_protocol=env.get("UCI_LLM_PROTOCOL", "ollama").lower(),
            llm_url=env.get("UCI_LLM_URL", ""),
            llm_model=env.get("UCI_LLM_MODEL", ""),
            llm_timeout=int(_num(env, "UCI_LLM_TIMEOUT", 60)),
            llm_max_tokens=int(_num(env, "UCI_LLM_MAX_TOKENS", 700)),
            llm_log=env.get("UCI_LLM_LOG", ""),
            settings=_collect_settings(env),
        )
        if overrides:
            safe = {k: v for k, v in overrides.items() if k in cfg.__dataclass_fields__ and v is not None}
            cfg = replace(cfg, **safe)
        return cfg


def _num(env: dict[str, str], key: str, default: float) -> float:
    try:
        return float(env[key]) if key in env else float(default)
    except (TypeError, ValueError):
        return float(default)


def _default_model_for(provider: str, env: dict[str, str]) -> tuple[str, int]:
    if provider == "local":
        return "hash-64", 64
    if provider == "noop":
        return "noop", 0
    if provider == "ollama":
        return env.get("UCI_OLLAMA_MODEL", "nomic-embed-text"), 768
    if provider == "openai":
        return env.get("UCI_OPENAI_EMBED_MODEL", "text-embedding-3-small"), 1536
    return "hash-64", 64


def _gap_prefixes(env: dict[str, str]) -> tuple[str, ...]:
    extra = env.get("UCI_GAP_EXTERNAL_PREFIXES", "")
    prefixes = list(_DEFAULT_GAP_PREFIXES)
    prefixes.extend(p.strip() for p in extra.split(",") if p.strip())
    return tuple(dict.fromkeys(prefixes))


def _collect_settings(env: dict[str, str]) -> dict[str, Any]:
    """Pull optional backend connection settings from the environment."""
    keys = [
        "UCI_OLLAMA_BASE_URL", "UCI_OPENAI_API_KEY", "UCI_OPENAI_BASE_URL",
        "UCI_QDRANT_URL", "UCI_MEMGRAPH_URL", "UCI_NEO4J_URL", "UCI_NEO4J_USER",
        "UCI_NEO4J_PASSWORD", "UCI_POSTGRES_DSN", "UCI_LLM_API_KEY",
    ]
    out = {k[4:].lower(): v for k, v in env.items() if k in keys}
    # LSP edge-oracle config is per-language and open-ended (UCI_LSP_<LANG>_CMD,
    # UCI_LSP_<LANG>_COPYBOOKS, …) — collect by prefix (docs/lsp-refactoring-recommendations.md §2.2).
    out.update({k[4:].lower(): v for k, v in env.items() if k.startswith("UCI_LSP_")})
    return out


__all__ = ["Config", "DEFAULT_IGNORE_GLOBS"]
