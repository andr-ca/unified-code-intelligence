"""SCIP :class:`EdgeSource` — consume a batch cross-reference index as canonical edges.

SCIP (``index.scip``, produced by ``scip-python`` / ``scip-typescript`` / ``scip-java``) is the
batch-friendly cousin of live LSP: a one-shot protobuf artifact of precise cross-references, exactly
UCI's use case (docs/lsp-refactoring-recommendations.md §2.1). We read it with a tiny stdlib
protobuf decoder (no third-party dependency, same policy as the LSP/MCP transports) and map its
occurrences onto UCI entities by ``(path, line)``: a reference occurrence whose symbol is defined
elsewhere becomes a ``CALLS``/``REFERENCES`` edge with ``resolution="scip"`` and confidence 1.0.

Only the fields we need are decoded — Index→documents, Document→{relative_path, occurrences},
Occurrence→{range, symbol, symbol_roles} — so the reader is small and forward-compatible (unknown
fields are skipped).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Iterator

from ..core.entities import SYMBOL_KINDS, EntityType
from ..core.ids import relationship_id
from ..core.provenance import Provenance
from ..core.relationships import RelationType, Relationship
from .base import Budget, EdgeDelta, EdgeSource

_ROLE_DEFINITION = 0x1  # SymbolRole.Definition bit
_CALLABLE = {EntityType.FUNCTION, EntityType.METHOD, EntityType.LEGACY_PROGRAM, EntityType.PARAGRAPH}


# ------------------------------------------------------------- protobuf-lite
def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _iter_fields(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple[int, int, Any]]:
    """Yield ``(field_number, wire_type, value)`` for a protobuf message region. Length-delimited
    values are returned as raw ``bytes``; varints/fixed as ints/bytes."""
    end = len(data) if end is None else end
    pos = start
    while pos < end:
        tag, pos = _read_varint(data, pos)
        field_no, wire = tag >> 3, tag & 0x7
        if wire == 0:
            val, pos = _read_varint(data, pos)
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            val, pos = data[pos:pos + length], pos + length
        elif wire == 1:
            val, pos = data[pos:pos + 8], pos + 8
        elif wire == 5:
            val, pos = data[pos:pos + 4], pos + 4
        else:
            raise ValueError(f"unsupported wire type {wire} at {pos}")
        yield field_no, wire, val


def _packed_varints(data: bytes) -> list[int]:
    out, pos = [], 0
    while pos < len(data):
        v, pos = _read_varint(data, pos)
        out.append(v)
    return out


@dataclass
class ScipOccurrence:
    range: list[int]       # [startLine, startChar, endLine?, endChar] (0-based)
    symbol: str
    roles: int

    @property
    def start_line(self) -> int:
        return self.range[0] if self.range else 0

    @property
    def is_definition(self) -> bool:
        return bool(self.roles & _ROLE_DEFINITION)


@dataclass
class ScipDocument:
    relative_path: str = ""
    occurrences: list[ScipOccurrence] = dc_field(default_factory=list)


def parse_scip_index(data: bytes) -> list[ScipDocument]:
    """Decode a ``.scip`` byte blob into documents with their occurrences (best-effort, tolerant)."""
    docs: list[ScipDocument] = []
    for field_no, wire, val in _iter_fields(data):
        if field_no == 2 and wire == 2:  # Index.documents (repeated Document)
            docs.append(_parse_document(val))
    return docs


def _parse_document(data: bytes) -> ScipDocument:
    doc = ScipDocument()
    for field_no, wire, val in _iter_fields(data):
        if field_no == 1 and wire == 2:       # relative_path
            doc.relative_path = val.decode("utf-8", errors="replace")
        elif field_no == 2 and wire == 2:     # occurrences (repeated Occurrence)
            doc.occurrences.append(_parse_occurrence(val))
    return doc


def _parse_occurrence(data: bytes) -> ScipOccurrence:
    rng: list[int] = []
    symbol = ""
    roles = 0
    for field_no, wire, val in _iter_fields(data):
        if field_no == 1 and wire == 2:       # range (packed int32)
            rng = _packed_varints(val)
        elif field_no == 2 and wire == 2:     # symbol
            symbol = val.decode("utf-8", errors="replace")
        elif field_no == 3 and wire == 0:     # symbol_roles
            roles = val
    return ScipOccurrence(range=rng, symbol=symbol, roles=roles)


# ------------------------------------------------------------- edge source
class ScipSource(EdgeSource):
    name = "scip"

    def __init__(self, index_path: str, repo_path: str, version: str = "scip"):
        self.index_path = str(index_path)
        self.repo_path = str(Path(repo_path).resolve())
        self.version = version
        self._loc_index: dict[str, list[tuple[int, str]]] = {}

    @property
    def available(self) -> bool:
        return Path(self.index_path).exists()

    def discover(self, graph, repo_id: str, worklist=None, budget: Budget | None = None) -> EdgeDelta:
        delta = EdgeDelta()
        if not self.available:
            return delta
        try:
            docs = parse_scip_index(Path(self.index_path).read_bytes())
        except (OSError, ValueError, IndexError):
            return delta  # corrupt/foreign index → produce nothing, never crash the run
        budget = budget or Budget()
        # 1. symbol → its definition site (path, line0), from all definition occurrences.
        def_site: dict[str, tuple[str, int]] = {}
        for doc in docs:
            for occ in doc.occurrences:
                if occ.is_definition and occ.symbol:
                    def_site[occ.symbol] = (doc.relative_path, occ.start_line)
        # 2. each reference occurrence → an edge from its enclosing entity to the defined entity.
        seen: set[str] = set()
        for doc in docs:
            for occ in doc.occurrences:
                if occ.is_definition or occ.symbol not in def_site:
                    continue
                def_path, def_line0 = def_site[occ.symbol]
                dst = self._entity_at(graph, def_path, def_line0 + 1)
                src = self._enclosing_entity(graph, doc.relative_path, occ.start_line + 1)
                if dst is None or src is None or src.id == dst.id:
                    continue
                if not budget.spend():
                    return delta
                delta.queried += 1
                rtype = RelationType.CALLS if dst.kind in _CALLABLE else RelationType.REFERENCES
                rid = relationship_id(rtype, src.id, dst.id, ordinal=occ.start_line + 1)
                if rid in seen:
                    continue
                seen.add(rid)
                prov = Provenance(repo_id, doc.relative_path, occ.start_line + 1, occ.start_line + 1,
                                  self.extractor, 1.0)
                delta.discovered.append(Relationship(
                    id=rid, type=rtype, src_id=src.id, dst_id=dst.id, provenance=prov,
                    attributes={"resolution": "scip", "symbol": occ.symbol}))
        return delta

    # -- location → entity mapping (shared shape with LspEdgeSource) --------
    def _entities_in(self, graph, path: str) -> list[tuple[int, str]]:
        if path not in self._loc_index:
            self._loc_index[path] = sorted(
                (e.provenance.start_line, e.id)
                for e in graph.entities()
                if e.provenance.path == path and e.provenance.start_line)
        return self._loc_index[path]

    def _entity_at(self, graph, path: str, line: int):
        for start_line, eid in self._entities_in(graph, path):
            if abs(start_line - line) <= 1:
                return graph.get_entity(eid)
        return None

    def _enclosing_entity(self, graph, path: str, line: int):
        """The narrowest symbol whose definition starts at or before ``line`` (its container)."""
        best = None
        for start_line, eid in self._entities_in(graph, path):
            if start_line <= line:
                ent = graph.get_entity(eid)
                if ent and ent.kind in SYMBOL_KINDS:
                    best = ent
            else:
                break
        return best


__all__ = ["ScipSource", "parse_scip_index", "ScipDocument", "ScipOccurrence"]
