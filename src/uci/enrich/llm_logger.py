"""Append-only JSONL log of every LLM call, for offline analysis (docs/llm-enrichment.md §2.1).

One line per completion — prompt, response, latency, model — so a run can be replayed, diffed,
or mined after the fact (which model hallucinated, how long the cloud tier took, how many tokens a
pass really cost). Design constraints:

  - **Never logs the API key** (the record is built from the caller's arguments, not the client's
    secret; :meth:`LlmClient.describe` is the only key-aware surface and it already redacts).
  - **Best-effort** — a logging failure (full disk, bad path) must never break an LLM call, so
    every write is wrapped and swallowed.
  - **Opt-out, not opt-in** — enabled by default to ``<store_dir>/llm-calls.jsonl`` so calls are
    captured without ceremony; set ``UCI_LLM_LOG=off`` (or ``0``/``false``/``none``) to disable.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

_DISABLED = {"", "off", "0", "false", "no", "none"}


class LlmCallLogger:
    """Thread-safe JSONL appender. Construct via :meth:`from_config` (handles the on/off rules)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, config) -> "LlmCallLogger | None":
        """Return a logger, or ``None`` when logging is disabled. ``config.llm_log`` may be an
        explicit path, empty (→ default under the store dir), or a disable sentinel."""
        raw = str(getattr(config, "llm_log", "") or "").strip()
        if raw.lower() in _DISABLED and raw != "":
            return None
        if raw == "":
            try:
                path = config.store_dir / "llm-calls.jsonl"
            except (AttributeError, TypeError):
                return None
        else:
            path = Path(raw)
        return cls(path)

    def log(self, *, protocol: str, model: str, tag: str, max_tokens: int, latency_ms: int,
            ok: bool, system: str, user: str, response: str, error: str | None = None) -> None:
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "protocol": protocol,
            "model": model,
            "tag": tag,
            "max_tokens": max_tokens,
            "latency_ms": latency_ms,
            "ok": ok,
            "error": error,
            "system_chars": len(system),
            "user_chars": len(user),
            "response_chars": len(response),
            "system": system,
            "user": user,
            "response": response,
        }
        line = json.dumps(record, ensure_ascii=False)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError:
            pass  # logging is best-effort; never break the LLM call


def env_log_default() -> str:
    """The raw ``UCI_LLM_LOG`` value (empty string when unset), for Config wiring."""
    return os.environ.get("UCI_LLM_LOG", "")


__all__ = ["LlmCallLogger", "env_log_default"]
