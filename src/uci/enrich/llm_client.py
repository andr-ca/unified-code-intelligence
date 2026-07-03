"""Protocol-pluggable LLM client for enrichment (docs/llm-enrichment.md §2).

Speaks three wire protocols over plain stdlib HTTP — no SDK dependency, consistent with the
local-lite philosophy:

  - ``ollama``    — native ``/api/chat`` (default; local, keyless)
  - ``openai``    — ``/chat/completions`` (OpenAI itself and every compatible server:
                    vLLM, LM Studio, LiteLLM, gateways)
  - ``anthropic`` — ``/v1/messages``
  - ``freellm``   — ``/chat/completions`` at a local OpenAI-compatible gateway
                    (default ``localhost:3001/v1``; empty model → the gateway auto-selects)

Configuration comes from :class:`uci.config.Config`: ``llm_protocol``, ``llm_url``,
``llm_model``, ``llm_timeout``, ``llm_max_tokens``, and the optional API key in
``settings["llm_api_key"]`` (never logged, never serialized).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from ..config import Config
from .llm_logger import LlmCallLogger

#: HTTP statuses worth retrying: rate limit + transient server errors (cloud free tiers hit these).
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 529})
_MAX_RETRIES = 3

_DEFAULTS = {
    "ollama": {"url": "http://localhost:11434", "model": "qwen2.5-coder:7b"},
    "openai": {"url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "anthropic": {"url": "https://api.anthropic.com", "model": "claude-haiku-4-5-20251001"},
    # local OpenAI-compatible gateway; empty model lets the gateway pick the best available model
    "freellm": {"url": "http://localhost:3001/v1", "model": ""},
}

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LlmError(RuntimeError):
    """Raised when the provider is unreachable or returns an unusable response."""


class LlmClient:
    def __init__(self, config: Config) -> None:
        protocol = (config.llm_protocol or "ollama").lower()
        if protocol not in _DEFAULTS:
            raise LlmError(
                f"unknown llm protocol {protocol!r} (ollama | openai | anthropic | freellm)")
        self.protocol = protocol
        self.base_url = (config.llm_url or _DEFAULTS[protocol]["url"]).rstrip("/")
        self.model = config.llm_model or _DEFAULTS[protocol]["model"]
        self.timeout = config.llm_timeout
        self.max_tokens = config.llm_max_tokens
        self._api_key = config.settings.get("llm_api_key", "")
        self._logger = LlmCallLogger.from_config(config)
        #: attribution for the call log when a caller doesn't pass an explicit ``tag``
        #: (e.g. the enricher sets it per pass, the LLM-eval per task).
        self.default_tag = ""

    # -- transport -----------------------------------------------------------
    def _post(self, path: str, payload: dict, headers: dict[str, str]) -> dict:
        data = json.dumps(payload).encode("utf-8")
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            req = urllib.request.Request(
                f"{self.base_url}{path}", data=data,
                headers={"Content-Type": "application/json", **headers},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                last_exc = LlmError(f"{self.protocol} HTTP {exc.code}: {detail}")
                if exc.code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                    time.sleep(self._retry_after(exc, attempt))
                    continue
                raise last_exc from exc
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                last_exc = LlmError(f"{self.protocol} at {self.base_url}: {exc}")
                if attempt < _MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                raise last_exc from exc
        raise last_exc or LlmError("request failed")  # pragma: no cover

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
        """Honor a Retry-After header when present; else exponential backoff (2/4/8s)."""
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                return min(float(header), 30.0)
            except ValueError:
                pass
        return float(2 ** (attempt + 1))

    # -- public --------------------------------------------------------------
    @property
    def available(self) -> bool:
        """Cheap reachability probe (no tokens consumed where possible)."""
        try:
            if self.protocol == "ollama":
                urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3)  # noqa: S310
                return True
            # cloud protocols: consider configured == available (probing costs a request)
            return bool(self._api_key) or self.protocol == "openai" and "localhost" in self.base_url
        except (urllib.error.URLError, OSError):
            return False

    def complete(self, system: str, user: str, max_tokens: int | None = None,
                 tag: str = "") -> str:
        """One chat completion, deterministic settings (temperature 0). Every call is logged
        (prompt, response, latency, model — never the key) when logging is enabled; ``tag``
        attributes the call to a pass/task for offline analysis (docs/llm-enrichment.md §2.1)."""
        tokens = max_tokens or self.max_tokens
        tag = tag or self.default_tag
        started = time.perf_counter()
        try:
            response = self._complete_raw(system, user, tokens)
        except Exception as exc:  # noqa: BLE001 - log then re-raise, logging must not swallow
            self._log(tag, tokens, started, ok=False, system=system, user=user,
                      response="", error=str(exc))
            raise
        self._log(tag, tokens, started, ok=True, system=system, user=user, response=response)
        return response

    def _log(self, tag: str, tokens: int, started: float, *, ok: bool, system: str,
             user: str, response: str, error: str | None = None) -> None:
        if self._logger is None:
            return
        self._logger.log(
            protocol=self.protocol, model=self.model, tag=tag, max_tokens=tokens,
            latency_ms=int((time.perf_counter() - started) * 1000), ok=ok,
            system=system, user=user, response=response, error=error)

    def _complete_raw(self, system: str, user: str, tokens: int) -> str:
        if self.protocol == "ollama":
            data = self._post("/api/chat", {
                "model": self.model, "stream": False,
                # thinking models (qwen3+, deepseek-r1) otherwise burn the whole token budget
                # on the `thinking` field and return empty content (done_reason=length)
                "think": False,
                "options": {"temperature": 0, "num_predict": tokens},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            }, {})
            message = data.get("message") or {}
            content = message.get("content", "")
            if not content and message.get("thinking"):
                raise LlmError(
                    f"model {self.model} spent its token budget thinking and returned no "
                    f"content — raise UCI_LLM_MAX_TOKENS or use a non-thinking model")
            return content
        if self.protocol in ("openai", "freellm"):
            headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
            payload: dict = {
                "temperature": 0, "max_tokens": tokens,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            }
            if self.model:  # freellm may leave the model empty to auto-select the best one
                payload["model"] = self.model
            data = self._post("/chat/completions", payload, headers)
            choices = data.get("choices") or []
            return choices[0].get("message", {}).get("content", "") if choices else ""
        # anthropic
        data = self._post("/v1/messages", {
            "model": self.model, "max_tokens": tokens, "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }, {"x-api-key": self._api_key, "anthropic-version": "2023-06-01"})
        blocks = data.get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def complete_json(self, system: str, user: str, max_tokens: int | None = None,
                      tag: str = "") -> Any:
        """Completion parsed as JSON (tolerates code fences and surrounding prose)."""
        text = self.complete(system, user, max_tokens, tag=tag).strip()
        for candidate in self._json_candidates(text):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        raise LlmError(f"model did not return parseable JSON: {text[:200]!r}")

    @staticmethod
    def _json_candidates(text: str):
        m = _JSON_BLOCK.search(text)
        if m:
            yield m.group(1).strip()
        yield text
        # last resort: the first {...} or [...] span
        for open_c, close_c in (("{", "}"), ("[", "]")):
            start, end = text.find(open_c), text.rfind(close_c)
            if 0 <= start < end:
                yield text[start:end + 1]

    def describe(self) -> dict:
        """Config surface for logs/CLI — never includes the key."""
        return {"protocol": self.protocol, "url": self.base_url, "model": self.model,
                "api_key_set": bool(self._api_key)}


__all__ = ["LlmClient", "LlmError"]
