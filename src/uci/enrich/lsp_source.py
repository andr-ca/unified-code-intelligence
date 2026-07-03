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

from ..core.entities import SYMBOL_KINDS
from ..core.ids import relationship_id
from ..core.provenance import Provenance
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

    # -- discover ----------------------------------------------------------
    def discover(self, graph, repo_id: str, worklist, budget: Budget | None = None) -> EdgeDelta:
        """Turn ``unresolved_calls`` worklist sites into edges the static extractor missed: resolve
        the call target with the server and, if it lands on a known entity, emit a new provable
        ``CALLS`` edge (resolution ``lsp-verified``)."""
        delta = EdgeDelta()
        client = self._ensure_client()
        if client is None:
            return delta
        budget = budget or Budget()
        opened: set[str] = set()
        for site in worklist or []:
            path = str(site.get("path", ""))
            if not path or not self._handles(path):
                continue
            name = str(site.get("name", ""))
            line = int(site.get("line", 0) or 0)
            if line <= 0:
                continue
            caller = self._caller_entity(graph, site)
            if caller is None:
                continue
            char = self._call_site_column(path, line, name) if name else 0
            if char is None:
                continue
            if not budget.spend():
                return delta
            try:
                result = self._definition(client, path, line - 1, char, opened)
            except LspError:
                break
            delta.queried += 1
            target = self._location_entity(graph, result)
            if target is None or target.id == caller.id:
                continue
            rid = relationship_id(RelationType.CALLS, caller.id, target.id, ordinal=line)
            prov = Provenance(repo_id, path, line, line, self.extractor, 0.95)
            delta.discovered.append(Relationship(
                id=rid, type=RelationType.CALLS, src_id=caller.id, dst_id=target.id,
                provenance=prov,
                attributes={"resolution": "lsp-verified", "discovered_by": self.extractor,
                            "mode": "discover", "via": name}))
        return delta

    # -- complete ----------------------------------------------------------
    def complete(self, graph, repo_id: str, symbols, budget: Budget | None = None) -> EdgeDelta:
        """Fill type-aware ``REFERENCES`` edges for high-value symbols: ask the server for every
        reference to each symbol and connect the *enclosing* entity of each reference to it."""
        delta = EdgeDelta()
        client = self._ensure_client()
        if client is None:
            return delta
        budget = budget or Budget()
        opened: set[str] = set()
        for sym in symbols or []:
            if sym is None or not sym.provenance.path or not self._handles(sym.provenance.path):
                continue
            char = self._call_site_column(sym.provenance.path, sym.provenance.start_line, sym.name)
            if char is None:
                continue
            if not budget.spend():
                return delta
            try:
                refs = self._references(client, sym.provenance.path,
                                        sym.provenance.start_line - 1, char, opened)
            except LspError:
                break
            delta.queried += 1
            seen: set[str] = set()
            for loc in _locations(refs):
                loc_path = _uri_to_relpath(loc.get("uri", ""), self.repo_path)
                loc_line = int(loc.get("range", {}).get("start", {}).get("line", -1)) + 1
                if not loc_path:
                    continue
                enclosing = self._enclosing_entity(graph, loc_path, loc_line)
                if enclosing is None or enclosing.id == sym.id:
                    continue
                rid = relationship_id(RelationType.REFERENCES, enclosing.id, sym.id, ordinal=loc_line)
                if rid in seen:
                    continue
                seen.add(rid)
                prov = Provenance(repo_id, loc_path, loc_line, loc_line, self.extractor, 0.95)
                delta.discovered.append(Relationship(
                    id=rid, type=RelationType.REFERENCES, src_id=enclosing.id, dst_id=sym.id,
                    provenance=prov,
                    attributes={"resolution": "lsp-verified", "discovered_by": self.extractor,
                                "mode": "complete"}))
        return delta

    # -- helpers -----------------------------------------------------------
    def _definition(self, client, path: str, line0: int, char: int, opened: set[str]) -> Any:
        abs_path = str(Path(self.repo_path) / path)
        if path not in opened:
            client.did_open(abs_path, self.spec.language_id, "\n".join(self._lines(path)))
            opened.add(path)
        return client.definition(abs_path, line0, char)

    def _references(self, client, path: str, line0: int, char: int, opened: set[str]) -> Any:
        abs_path = str(Path(self.repo_path) / path)
        if path not in opened:
            client.did_open(abs_path, self.spec.language_id, "\n".join(self._lines(path)))
            opened.add(path)
        return client.references(abs_path, line0, char)

    def _caller_entity(self, graph, site: dict):
        qname = str(site.get("caller", ""))
        if not qname:
            return None
        for ent in graph.find_by_name(qname.split(".")[-1]):
            if ent.qualified_name == qname:
                return ent
        return None

    def _location_entity(self, graph, result: Any):
        loc = _first_location(result)
        if loc is None:
            return None
        loc_path = _uri_to_relpath(loc.get("uri", ""), self.repo_path)
        loc_line = int(loc.get("range", {}).get("start", {}).get("line", -1)) + 1
        if not loc_path:
            return None
        return self._entity_at(graph, loc_path, loc_line)

    def _enclosing_entity(self, graph, path: str, line: int):
        """The narrowest symbol whose definition starts at or before ``line`` (its container)."""
        if path not in self._loc_index:
            self._loc_index[path] = sorted(
                (e.provenance.start_line, e.id)
                for e in graph.entities() if e.provenance.path == path and e.provenance.start_line)
        best = None
        for start_line, eid in self._loc_index[path]:
            if start_line <= line:
                ent = graph.get_entity(eid)
                if ent and ent.kind in SYMBOL_KINDS:
                    best = ent
            else:
                break
        return best

    # -- verify helpers ----------------------------------------------------
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


def _locations(result: Any) -> list[dict[str, Any]]:
    """Normalize a ``references``/definition result into a flat list of Location dicts."""
    if not result:
        return []
    if isinstance(result, dict):
        loc = _first_location(result)
        return [loc] if loc else []
    out: list[dict[str, Any]] = []
    for item in result:
        loc = _first_location(item)
        if loc:
            out.append(loc)
    return out


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
