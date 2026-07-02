"""Reciprocal Rank Fusion — robust combination of incomparable ranked signals (from CodeRAG)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence


def reciprocal_rank_fusion(
    ranked_lists: Mapping[str, Sequence[str]],
    weights: Mapping[str, float] | None = None,
    k: int = 60,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Fuse per-signal ranked id lists into a single score map.

    Returns ``(scores, membership)`` where ``membership[id]`` lists the signals that contributed to
    that id (used to explain *why* a result was included).
    """
    weights = weights or {}
    scores: dict[str, float] = defaultdict(float)
    membership: dict[str, list[str]] = defaultdict(list)
    for signal, ids in ranked_lists.items():
        weight = weights.get(signal, 1.0)
        if weight == 0.0:
            continue
        for rank, item_id in enumerate(ids):
            scores[item_id] += weight / (k + rank + 1)
            if signal not in membership[item_id]:
                membership[item_id].append(signal)
    return dict(scores), dict(membership)


__all__ = ["reciprocal_rank_fusion"]
