"""Retrieval result types shared across CLI, MCP, and API surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.entities import Entity


@dataclass
class RetrievalHit:
    entity_id: str
    kind: str
    name: str
    qualified_name: str
    path: str
    start_line: int
    end_line: int
    score: float
    signals: list[str] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.5
    relationship_path: list[str] = field(default_factory=list)
    resolution: str = ""
    missing: bool = False
    external: bool = False

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "score": round(self.score, 5),
            "signals": self.signals,
            "reason": self.reason,
            "confidence": self.confidence,
            "relationship_path": self.relationship_path,
            "resolution": self.resolution,
            "missing": self.missing,
            "external": self.external,
        }

    @classmethod
    def from_entity(
        cls,
        entity: Entity,
        score: float,
        signals: list[str],
        reason: str,
        confidence: float = 0.5,
        relationship_path: list[str] | None = None,
    ) -> RetrievalHit:
        return cls(
            entity_id=entity.id,
            kind=entity.kind.value,
            name=entity.name,
            qualified_name=entity.qualified_name,
            path=entity.provenance.path,
            start_line=entity.provenance.start_line,
            end_line=entity.provenance.end_line,
            score=score,
            signals=signals,
            reason=reason,
            confidence=confidence,
            relationship_path=relationship_path or [],
            missing=bool(entity.attributes.get("missing")),
            external=bool(entity.attributes.get("external")),
        )


__all__ = ["RetrievalHit"]
