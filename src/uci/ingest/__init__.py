"""``uci.ingest`` — repository scanning, change detection, git metadata, and graph normalization."""

from __future__ import annotations

from .git_meta import collect_commits, compute_churn, is_git_repo
from .graph_builder import FileParse, GraphBuilder
from .indexer import Indexer, IndexStats
from .langdetect import detect_language, is_code, module_qname
from .scanner import ScannedFile, scan

__all__ = [
    "Indexer",
    "IndexStats",
    "GraphBuilder",
    "FileParse",
    "scan",
    "ScannedFile",
    "detect_language",
    "module_qname",
    "is_code",
    "collect_commits",
    "compute_churn",
    "is_git_repo",
]
