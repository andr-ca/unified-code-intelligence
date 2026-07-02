"""Provenance: every canonical fact traces back to a file and line range.

This is a first-class principle of UCI — nodes and edges are only trustworthy if you can
point at the exact source that produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Provenance:
    """Where an entity or relationship came from.

    Attributes:
        repo_id: Stable id of the repository the fact belongs to.
        path: Repository-relative path of the source file (never absolute — see path sanitization).
        start_line: 1-based inclusive start line, or 0 when not line-specific.
        end_line: 1-based inclusive end line, or 0 when not line-specific.
        extractor: Name of the component that produced the fact (e.g. "python_parser").
        confidence: 0.0-1.0 confidence that the fact is correct.
    """

    repo_id: str
    path: str = ""
    start_line: int = 0
    end_line: int = 0
    extractor: str = "unknown"
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "extractor": self.extractor,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        return cls(
            repo_id=data.get("repo_id", ""),
            path=data.get("path", ""),
            start_line=int(data.get("start_line", 0) or 0),
            end_line=int(data.get("end_line", 0) or 0),
            extractor=data.get("extractor", "unknown"),
            confidence=float(data.get("confidence", 1.0)),
        )

    @property
    def line_range(self) -> tuple[int, int]:
        return (self.start_line, self.end_line)

    def location(self) -> str:
        """Human-readable ``path:start-end`` citation."""
        if not self.path:
            return self.repo_id
        if self.start_line:
            if self.end_line and self.end_line != self.start_line:
                return f"{self.path}:{self.start_line}-{self.end_line}"
            return f"{self.path}:{self.start_line}"
        return self.path


UNKNOWN_PROVENANCE = Provenance(repo_id="", extractor="unknown", confidence=0.0)


def merge_attributes(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge two attribute dicts, skipping ``None`` values from *extra*."""
    out: dict[str, Any] = dict(base or {})
    for key, value in (extra or {}).items():
        if value is not None:
            out[key] = value
    return out


# Re-exported for convenience so ``from uci.core.provenance import field`` never surprises callers.
__all__ = ["Provenance", "UNKNOWN_PROVENANCE", "merge_attributes", "field"]
