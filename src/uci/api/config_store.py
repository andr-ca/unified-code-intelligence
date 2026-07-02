"""Per-project configuration overrides for the dashboard's Config tab.

``Config.from_env(repo, overrides=...)`` already lets any dataclass field be overridden, but nothing
persists those overrides. This module stores them at ``<repo>/.uci/overrides.json`` and the
:class:`~uci.api.projects.ProjectManager` feeds them back in when it opens a project's engine — so a
change in the UI survives restarts and takes effect on the next engine open. Kept in the API layer so
it never edits the (reviewer-owned) ``config.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FLOAT = {"weight_symbol", "weight_keyword", "weight_semantic", "weight_graph",
          "weight_proximity", "weight_churn"}
_INT = {"embedding_dim", "rrf_k", "max_file_bytes", "max_chunk_lines", "window_lines", "window_overlap",
        "llm_timeout", "llm_max_tokens"}
_BOOL = {"use_gitignore", "index_all_text"}
_STR = {"profile", "graph_backend", "vector_backend", "metadata_backend",
        "embedding_provider", "embedding_model", "llm_protocol", "llm_url", "llm_model"}
_LIST = {"gap_external_prefixes"}
EDITABLE = _FLOAT | _INT | _BOOL | _STR | _LIST

#: Changing these requires a re-index to fully take effect (embeddings / what gets scanned).
REINDEX_FIELDS = {"embedding_provider", "embedding_model", "embedding_dim", "use_gitignore",
                  "index_all_text", "max_file_bytes", "max_chunk_lines", "window_lines",
                  "window_overlap", "gap_external_prefixes"}


def overrides_path(repo_path: str) -> Path:
    return Path(repo_path) / ".uci" / "overrides.json"


def load_overrides(repo_path: str) -> dict[str, Any]:
    try:
        data = json.loads(overrides_path(repo_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in data.items() if k in EDITABLE} if isinstance(data, dict) else {}


def _coerce(field: str, value: Any) -> Any:
    if field in _FLOAT:
        return float(value)
    if field in _INT:
        return int(value)
    if field in _BOOL:
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    if field in _LIST:
        items = value if isinstance(value, list) else str(value).split(",")
        return [str(p).strip() for p in items if str(p).strip()]
    return str(value)


def save_overrides(repo_path: str, incoming: dict[str, Any]) -> dict[str, Any]:
    """Validate + coerce the editable subset and persist. Returns the stored overrides."""
    if not isinstance(incoming, dict):
        raise ValueError("config payload must be an object")
    out: dict[str, Any] = {}
    for key, value in incoming.items():
        if key not in EDITABLE or value is None or value == "":
            continue
        try:
            out[key] = _coerce(key, value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid value for {key}: {exc}") from exc
    path = overrides_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out


def config_dict(cfg) -> dict[str, Any]:
    """Full, UI-friendly view of a :class:`Config` (read-only paths + every editable field)."""
    return {
        "repo_path": str(cfg.repo_path),
        "store_dir": str(cfg.store_dir),
        "profile": cfg.profile,
        "graph_backend": cfg.graph_backend,
        "vector_backend": cfg.vector_backend,
        "metadata_backend": cfg.metadata_backend,
        "embedding_provider": cfg.embedding_provider,
        "embedding_model": cfg.embedding_model,
        "embedding_dim": cfg.embedding_dim,
        "use_gitignore": cfg.use_gitignore,
        "index_all_text": cfg.index_all_text,
        "max_file_bytes": cfg.max_file_bytes,
        "max_chunk_lines": cfg.max_chunk_lines,
        "window_lines": cfg.window_lines,
        "window_overlap": cfg.window_overlap,
        "gap_external_prefixes": list(cfg.gap_external_prefixes),
        "weight_symbol": cfg.weight_symbol,
        "weight_keyword": cfg.weight_keyword,
        "weight_semantic": cfg.weight_semantic,
        "weight_graph": cfg.weight_graph,
        "weight_proximity": cfg.weight_proximity,
        "weight_churn": cfg.weight_churn,
        "rrf_k": cfg.rrf_k,
        "llm_protocol": getattr(cfg, "llm_protocol", "ollama"),
        "llm_url": getattr(cfg, "llm_url", ""),
        "llm_model": getattr(cfg, "llm_model", ""),
        "llm_timeout": getattr(cfg, "llm_timeout", 60),
        "llm_max_tokens": getattr(cfg, "llm_max_tokens", 700),
    }


__all__ = ["EDITABLE", "REINDEX_FIELDS", "overrides_path", "load_overrides",
           "save_overrides", "config_dict"]
