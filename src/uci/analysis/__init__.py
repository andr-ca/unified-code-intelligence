"""``uci.analysis`` — graph-derived summaries: overview, architecture, onboarding (no LLM required)."""

from __future__ import annotations

from .architecture import infer_architecture, layer_for_path
from .onboarding import onboarding_guide
from .overview import explain_module, repo_overview

__all__ = [
    "repo_overview",
    "explain_module",
    "infer_architecture",
    "layer_for_path",
    "onboarding_guide",
]
