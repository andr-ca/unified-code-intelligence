"""Generic LSP :class:`EdgeSource` — Verify mode over speculative call edges.

Given a language server (COBOL via Che4z, Python via pyright, …), this source takes UCI's
*speculative* edges — R4 ``name-match`` / R5 ``candidate`` calls the static extractor could only
guess — and asks the server ``textDocument/definition`` at each call site. If the definition lands
on the edge's declared target, the edge is **promoted** to ``resolution="lsp-verified"`` (confidence
0.95); if it lands on a *different* known entity, the edge is **pruned** with a tombstone; if the
server is unsure, the edge is left exactly as-is (we never prune on uncertainty). This is the
cheapest way to make the determinism claim true where name-resolution is weakest
(docs/lsp-refactoring-recommendations.md §2, §3.3).

The source is graceful: if the server binary is absent or fails to start, ``available`` is False and
``verify`` returns an empty delta — the enrich run logs a warning and moves on.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..core.relationships import RelationType, Relationship, RESOLVED_LEVELS
from .base import Budget, EdgeDelta, EdgeSource
from .lsp_client import LspClient, LspError
from .servers import ServerSpec, get_server

_VERIFY_TYPES = (RelationType.CALLS, RelationType.INVOKES)
_LINE_TOLERANCE = 1  # LSP definition line vs indexed start_line may differ by a header line


class LspEdgeSource(EdgeSource):
    def __init__(self, language: str, repo_path: str, settings: dict[str, Any] | None = None,
                 client: LspClient | None = None, spec: ServerSpec | None = None):
        self.language = language
        self.repo_path = str(Path(repo_path).resolve())
        self.settings = settings or {}
        self.spec = spec or get_server(language)
        self.name = f"lsp:{language}"
        self._client = client            # injected (tests) or lazily spawned
        self._client_injected = client is not None
        self._started = client is not None
        self._file_cache: dict[str, list[str]] = {}
        self._loc_index: dict[str, list[tuple[int, str]]] = {}  # path -> [(start_line, entity_id)]

    # -- availability / lifecycle ------------------------------------------
    @property
    def available(self) -> bool:
        if self._client is not None:
            return True
        return bool(self.spec and self.spec.resolved_cmd(self.settings))

    def _ensure_client(self) -> LspClient | None:
        if self._client is not None:
            return self._client
        if self._started or not self.spec:
            return None
        self._started = True
        cmd = self.spec.resolved_cmd(self.settings)
        if not cmd:
            return None
        try:
            client = LspClient.spawn(cmd, cwd=self.repo_path)
            client.initialize(self.repo_path,
                              initialization_options=self.spec.init_options(self.settings))
        except LspError:
            return None
        self._client = client
        return client

    def close(self) -> None:
        if self._client is not None and not self._client_injected:
            try:
                self._client.shutdown()
            except LspError:
                pass
        self._client = None

    # -- verify ------------------------------------------------------------
    def verify(self, graph, repo_id: str, budget: Budget | None = None) -> EdgeDelta:
        delta = EdgeDelta()
        client = self._ensure_client()
        if client is None:
            return delta
        budget = budget or Budget()
        opened: set[str] = set()
        for rtype in _VERIFY_TYPES:
            for rel in list(graph.relationships(rtype)):
                if not self._is_speculative(rel):
                    continue
                src = graph.get_entity(rel.src_id)
                dst = graph.get_entity(rel.dst_id)
                if src is None or dst is None:
                    continue
                path = rel.provenance.path or src.provenance.path
                if not self._handles(path):
                    continue
                char = self._call_site_column(path, rel.provenance.start_line, dst.name)
                if char is None:
                    continue  # can't locate the reference token → nothing to verify
                if not budget.spend():
                    return delta
                try:
                    if path not in opened:
                        client.did_open(str(Path(self.repo_path) / path), self.spec.language_id,
                                        "\n".join(self._lines(path)))
                        opened.add(path)
                    result = client.definition(str(Path(self.repo_path) / path),
                                               rel.provenance.start_line - 1, char)
                except LspError:
                    break  # server died mid-run: stop, keep whatever we have
                delta.queried += 1
                verdict = self._compare(graph, result, dst)
                if verdict == "confirm":
                    delta.promoted.append(self._promote(rel))
                elif verdict == "elsewhere":
                    delta.pruned.append(self._prune(rel))
        return delta

    # -- helpers -----------------------------------------------------------
    def _is_speculative(self, rel: Relationship) -> bool:
        res = rel.attributes.get("resolution", "")
        return bool(res) and res not in RESOLVED_LEVELS and not rel.attributes.get("pruned")

    def _handles(self, path: str) -> bool:
        return bool(self.spec) and path.lower().endswith(self.spec.suffixes)

    def _lines(self, path: str) -> list[str]:
        if path not in self._file_cache:
            try:
                text = (Path(self.repo_path) / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            self._file_cache[path] = text.splitlines()
        return self._file_cache[path]

    def _call_site_column(self, path: str, line_1based: int, name: str) -> int | None:
        lines = self._lines(path)
        if not (1 <= line_1based <= len(lines)):
            return None
        col = lines[line_1based - 1].lower().find(name.lower())
        return col if col >= 0 else None

    def _compare(self, graph, result: Any, dst) -> str:
        """Return 'confirm' | 'elsewhere' | 'unknown' by mapping the LSP definition location back to
        an entity. Conservative: prune only when the definition lands on a *different* known entity."""
        loc = _first_location(result)
        if loc is None:
            return "unknown"
        loc_path = _uri_to_relpath(loc.get("uri", ""), self.repo_path)
        loc_line = int(loc.get("range", {}).get("start", {}).get("line", -1)) + 1  # →1-based
        if not loc_path:
            return "unknown"
        if loc_path == dst.provenance.path and abs(loc_line - dst.provenance.start_line) <= _LINE_TOLERANCE:
            return "confirm"
        other = self._entity_at(graph, loc_path, loc_line)
        if other is not None and other.id != dst.id:
            return "elsewhere"
        return "unknown"

    def _entity_at(self, graph, path: str, line: int):
        if path not in self._loc_index:
            idx = [(e.provenance.start_line, e.id)
                   for e in graph.entities() if e.provenance.path == path and e.provenance.start_line]
            self._loc_index[path] = sorted(idx)
        best = None
        for start_line, eid in self._loc_index[path]:
            if abs(start_line - line) <= _LINE_TOLERANCE:
                best = graph.get_entity(eid)
        return best

    def _promote(self, rel: Relationship) -> Relationship:
        attrs = dict(rel.attributes)
        attrs["resolution"] = "lsp-verified"
        attrs["verified_by"] = self.extractor
        attrs.pop("pruned", None)
        prov = replace(rel.provenance, extractor=self.extractor, confidence=0.95)
        return Relationship(id=rel.id, type=rel.type, src_id=rel.src_id, dst_id=rel.dst_id,
                            provenance=prov, attributes=attrs)

    def _prune(self, rel: Relationship) -> Relationship:
        attrs = dict(rel.attributes)
        attrs["pruned"] = True
        attrs["pruned_by"] = self.extractor
        attrs["pruned_reason"] = "definition resolves to a different target"
        return Relationship(id=rel.id, type=rel.type, src_id=rel.src_id, dst_id=rel.dst_id,
                            provenance=rel.provenance, attributes=attrs)


def _first_location(result: Any) -> dict[str, Any] | None:
    """LSP definition may return a Location, a Location[], or a LocationLink[] — normalize to one."""
    if not result:
        return None
    if isinstance(result, dict):
        if "targetUri" in result:  # LocationLink
            return {"uri": result["targetUri"], "range": result.get("targetSelectionRange")
                    or result.get("targetRange", {})}
        return result
    if isinstance(result, list) and result:
        return _first_location(result[0])
    return None


def _uri_to_relpath(uri: str, repo_path: str) -> str:
    if not uri:
        return ""
    from urllib.parse import unquote, urlparse
    p = unquote(urlparse(uri).path)
    try:
        return str(Path(p).resolve().relative_to(Path(repo_path).resolve()))
    except ValueError:
        return ""


__all__ = ["LspEdgeSource"]
