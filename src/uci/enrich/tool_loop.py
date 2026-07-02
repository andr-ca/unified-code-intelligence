"""Bounded, read-only tool-loop for context-starved enrichment tasks (docs/agentic-enrichment.md).

A JSON-action protocol over plain chat completions (works identically on ollama / openai /
anthropic, no native tool-calling required). Hard-capped by the harness — max 3 tool calls,
120-line source slices, top-2 search — so it is an evidence-gathering loop, not an open agent.
The three tools are the engine's own read-only surfaces; nothing here can write or reach the
network beyond the LLM call itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core.entities import EntityType
from ..core.interfaces import GraphStore
from .llm_client import LlmClient, LlmError

MAX_TOOL_CALLS = 3
MAX_SOURCE_LINES = 120
MAX_SEARCH_RESULTS = 2
MAX_TRANSCRIPT_CHARS = 6000

_TOOL_PROTOCOL = (
    "\n\nYou may gather evidence before answering. Reply with EXACTLY ONE JSON object per turn:\n"
    '  {"action": "get_source", "path": "<repo-relative>", "start": <int>, "end": <int>}\n'
    '  {"action": "get_relationships", "name": "<entity name>"}\n'
    '  {"action": "search", "query": "<name fragment>"}\n'
    f"  {{\"action\": \"answer\", ...}}   (your final answer; required)\n"
    f"You get at most {MAX_TOOL_CALLS} tool calls. Pull the definition of an uncertain variable "
    "(e.g. its copybook or LINKAGE section) before deciding. When the variable's value is "
    "supplied by a caller/COMMAREA/LINKAGE and no concrete values are visible, answer with the "
    "empty result. No markdown, one JSON object only."
)


@dataclass
class ToolLoopResult:
    answer: dict
    transcript: list[dict] = field(default_factory=list)
    tool_calls: int = 0
    protocol_errors: int = 0

    def evidence_digest(self) -> str:
        import hashlib
        blob = json.dumps(self.transcript, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


class ToolLoop:
    """Runs the bounded loop for one task and returns the model's final ``answer`` payload."""

    def __init__(self, client: LlmClient, graph: GraphStore, repo_path: Path, repo_id: str) -> None:
        self.client = client
        self.graph = graph
        self.repo_path = Path(repo_path).resolve()
        self.repo_id = repo_id

    def run(self, system: str, user: str, answer_key: str, max_tokens: int = 400) -> ToolLoopResult:
        """Drive the loop. ``answer_key`` is the field the final answer must contain (e.g.
        ``candidates``); a turn is accepted as final iff it is ``{"action":"answer", ...}``."""
        result = ToolLoopResult(answer={})
        convo = f"{user}\n{_TOOL_PROTOCOL}"
        system = system + " Gather evidence with the tools before answering."
        for turn in range(MAX_TOOL_CALLS + 1):
            budget_left = MAX_TOOL_CALLS - result.tool_calls
            if budget_left <= 0:
                convo += "\n\nTOOL BUDGET EXHAUSTED — you must answer now with {\"action\":\"answer\", ...}."
            try:
                raw = self.client.complete_json(system, convo, max_tokens=max_tokens)
            except LlmError:
                # one nudge, then give up (caller treats empty answer as abstain)
                result.protocol_errors += 1
                if turn >= MAX_TOOL_CALLS:
                    break
                convo += "\n\nReply with a single valid JSON action object."
                continue
            if not isinstance(raw, dict) or "action" not in raw:
                result.protocol_errors += 1
                convo += "\n\nReply with a single valid JSON action object (must have \"action\")."
                continue
            action = raw.get("action")
            if action == "answer":
                result.answer = raw
                return result
            if budget_left <= 0:
                # spent budget but didn't answer — force one more loop that must answer
                convo += "\n\nYou must use action \"answer\" now."
                continue
            observation = self._dispatch(raw)
            result.tool_calls += 1
            result.transcript.append({"request": raw, "result": observation})
            convo += (f"\n\nTOOL RESULT ({result.tool_calls}/{MAX_TOOL_CALLS}) for "
                      f"{json.dumps(raw)}:\n{observation}")
            if len(convo) > MAX_TRANSCRIPT_CHARS * 2:  # keep context bounded (oldest-first trim)
                convo = convo[-MAX_TRANSCRIPT_CHARS * 2:]
        return result

    # -- tools (read-only, clamped) -----------------------------------------
    def _dispatch(self, req: dict) -> str:
        action = req.get("action")
        try:
            if action == "get_source":
                return self._get_source(req.get("path", ""),
                                        int(req.get("start", 1)), int(req.get("end", 1)))
            if action == "get_relationships":
                return self._get_relationships(str(req.get("name", "")))
            if action == "search":
                return self._search(str(req.get("query", "")))
        except (ValueError, TypeError) as exc:
            return f"error: {exc}"
        return f"error: unknown action {action!r}"

    def _get_source(self, path: str, start: int, end: int) -> str:
        full = (self.repo_path / path).resolve()
        if not str(full).startswith(str(self.repo_path)):  # clamp to repo root
            return "error: path outside repository"
        try:
            lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return f"error: cannot read {path}"
        start = max(1, start)
        end = min(len(lines), max(start, end))
        if end - start + 1 > MAX_SOURCE_LINES:
            end = start + MAX_SOURCE_LINES - 1
        body = "\n".join(lines[start - 1:end])
        note = f" (truncated to {MAX_SOURCE_LINES} lines)" if end - start + 1 >= MAX_SOURCE_LINES else ""
        return f"{path}:{start}-{end}{note}\n{body}"

    def _get_relationships(self, name: str) -> str:
        ents = self.graph.find_by_name(name, exact=True) or self.graph.find_by_name(name, exact=False)
        if not ents:
            return f"no entity named {name!r}"
        ent = ents[0]
        out, inc = [], []
        for rel in self.graph.out_relationships(ent.id)[:12]:
            other = self.graph.get_entity(rel.dst_id)
            if other:
                out.append(f"{rel.type.value}->{other.name}")
        for rel in self.graph.in_relationships(ent.id)[:12]:
            other = self.graph.get_entity(rel.src_id)
            if other:
                inc.append(f"{other.name}->{rel.type.value}")
        return (f"{ent.qualified_name} ({ent.kind.value})\n"
                f"outgoing: {', '.join(out) or 'none'}\nincoming: {', '.join(inc) or 'none'}")

    def _search(self, query: str) -> str:
        seen: list[str] = []
        for exact in (True, False):
            for ent in self.graph.find_by_name(query, exact=exact):
                line = f"{ent.qualified_name} ({ent.kind.value}) {ent.provenance.path}"
                if line not in seen:
                    seen.append(line)
                if len(seen) >= MAX_SEARCH_RESULTS:
                    return "\n".join(seen)
        return "\n".join(seen) or f"no matches for {query!r}"


__all__ = ["ToolLoop", "ToolLoopResult", "MAX_TOOL_CALLS"]
