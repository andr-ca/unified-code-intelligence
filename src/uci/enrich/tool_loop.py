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
MAX_RAG_RESULTS = 5
MAX_FILE_LIST = 30
MAX_TRANSCRIPT_CHARS = 6000

_TOOL_LINES = {
    "get_source": '  {"action": "get_source", "path": "<repo-relative>", "start": <int>, "end": <int>}',
    "get_relationships": '  {"action": "get_relationships", "name": "<entity name>"}',
    "search": '  {"action": "search", "query": "<exact name fragment>"}',
    "rag_search": '  {"action": "rag_search", "query": "<natural-language question about the code>"}',
    "list_files": '  {"action": "list_files", "prefix": "<optional path prefix>"}',
}


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
    """Runs the bounded loop for one task and returns the model's final ``answer`` payload.

    Optional collaborators unlock extra tools: a ``retriever`` (the hybrid RAG) enables
    ``rag_search`` follow-up questions; a ``metadata`` store enables ``list_files``.
    ``max_tool_calls`` is per-instance (candidates: 3; agentic ask: 4).
    """

    def __init__(self, client: LlmClient, graph: GraphStore, repo_path: Path, repo_id: str,
                 retriever=None, metadata=None, max_tool_calls: int = MAX_TOOL_CALLS) -> None:
        self.client = client
        self.graph = graph
        self.repo_path = Path(repo_path).resolve()
        self.repo_id = repo_id
        self.retriever = retriever
        self.metadata = metadata
        self.max_tool_calls = max_tool_calls

    def _tools(self) -> list[str]:
        tools = ["get_source", "get_relationships", "search"]
        if self.retriever is not None:
            tools.append("rag_search")
        if self.metadata is not None:
            tools.append("list_files")
        return tools

    def _protocol(self) -> str:
        tools = self._tools()
        lines = "\n".join(_TOOL_LINES[t] for t in tools)
        # discovery hint: how to locate a copied member's file when you only know its name.
        find = "search" if "rag_search" not in tools else "rag_search (or search)"
        list_hint = " list_files shows every indexed path;" if "list_files" in tools else ""
        return (
            "\n\nYou may gather evidence before answering. Reply with EXACTLY ONE JSON object "
            f"per turn:\n{lines}\n"
            "  {\"action\": \"answer\", ...}   (your final answer; required)\n"
            f"You get at most {self.max_tool_calls} tool calls — spend them well:\n"
            f"- To resolve a variable set from a copied table (COBOL `COPY MEMBER`, or an index into "
            f"a table), find the copybook that defines it: {find} for the MEMBER or table name to "
            f"get its file path, then get_source that file to read the literal VALUEs.{list_hint}\n"
            "- get_source tells you the file's total length; if it says END OF FILE, do NOT re-read "
            "the same file — the content you want is in a DIFFERENT file (usually a copybook).\n"
            "- If the value comes from LINKAGE SECTION / DFHCOMMAREA / a caller (not a table you can "
            "read), it is opaque — answer with the empty/negative result.\n"
            "No markdown, one JSON object only."
        )

    def run(self, system: str, user: str, answer_key: str, max_tokens: int = 400) -> ToolLoopResult:
        """Drive the loop. ``answer_key`` is the field the final answer must contain (e.g.
        ``candidates``); a turn is accepted as final iff it is ``{"action":"answer", ...}``."""
        result = ToolLoopResult(answer={})
        convo = f"{user}\n{self._protocol()}"
        system = system + " Gather evidence with the tools before answering."
        for turn in range(self.max_tool_calls + 1):
            budget_left = self.max_tool_calls - result.tool_calls
            if budget_left <= 0:
                convo += "\n\nTOOL BUDGET EXHAUSTED — you must answer now with {\"action\":\"answer\", ...}."
            try:
                raw = self.client.complete_json(system, convo, max_tokens=max_tokens)
            except LlmError:
                # one nudge, then give up (caller treats empty answer as abstain)
                result.protocol_errors += 1
                if turn >= self.max_tool_calls:
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
            convo += (f"\n\nTOOL RESULT ({result.tool_calls}/{self.max_tool_calls}) for "
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
            if action == "rag_search" and self.retriever is not None:
                return self._rag_search(str(req.get("query", "")))
            if action == "list_files" and self.metadata is not None:
                return self._list_files(str(req.get("prefix", "")))
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
        total = len(lines)
        start = max(1, start)
        end = min(total, max(start, end))
        if end - start + 1 > MAX_SOURCE_LINES:
            end = start + MAX_SOURCE_LINES - 1
        body = "\n".join(lines[start - 1:end])
        # tell the model the file's real extent so it never re-reads the same slice hoping for more
        eof = " — END OF FILE, this is the whole file" if end >= total else \
              f" (truncated to {MAX_SOURCE_LINES} lines; ask for {end + 1}+ for more)" \
              if end - start + 1 >= MAX_SOURCE_LINES else ""
        return f"{path}:{start}-{end} of {total} lines{eof}\n{body}"

    def _rag_search(self, query: str) -> str:
        """Follow-up question against the full hybrid RAG (symbol+keyword+semantic+graph)."""
        hits = self.retriever.search(query, top_k=MAX_RAG_RESULTS)
        if not hits:
            return f"no results for {query!r}"
        lines = []
        for h in hits:
            line = f"{h.qualified_name} ({h.kind}) {h.path} — {h.reason}"
            if getattr(h, "summary", ""):
                line += f" | {h.summary[:120]}"
            lines.append(line)
        return "\n".join(lines)

    def _list_files(self, prefix: str) -> str:
        files = self.metadata.list_files(self.repo_id)
        rows = [f for f in files if not prefix or f["path"].startswith(prefix)]
        shown = rows[:MAX_FILE_LIST]
        out = "\n".join(f"{f['path']} ({f.get('language', '?')})" for f in shown)
        if len(rows) > MAX_FILE_LIST:
            out += f"\n... and {len(rows) - MAX_FILE_LIST} more (narrow the prefix)"
        return out or (f"no files under {prefix!r}" if prefix else "no files indexed")

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
        if seen:
            return "\n".join(seen)
        # no graph entity by that name (e.g. a data item like MENU-PGM lives inside a copybook,
        # not as its own node). Fall back to keyword/RAG so the query still finds the *file* that
        # contains it — the copybook the model actually needs — instead of a dead "no matches".
        if self.retriever is not None:
            hits = self.retriever.search(query, top_k=MAX_SEARCH_RESULTS)
            if hits:
                return ("no exact symbol; keyword matches (the name may be defined inside one of "
                        "these files):\n" + "\n".join(
                            f"{h.qualified_name} ({h.kind}) {h.path}" for h in hits))
        return f"no matches for {query!r}"


__all__ = ["ToolLoop", "ToolLoopResult", "MAX_TOOL_CALLS"]
