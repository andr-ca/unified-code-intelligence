"""Git metadata extraction via subprocess (optional — degrades gracefully without git)."""

from __future__ import annotations

import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

_UNIT = "\x1f"  # field sep
_REC = "\x1e"   # record sep


def is_git_repo(root: str | Path) -> bool:
    return (Path(root) / ".git").exists()


def head_sha(root: str | Path) -> str:
    """Current HEAD commit SHA, or empty string if unavailable."""
    if not is_git_repo(root):
        return ""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env dependent
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def commits_since(root: str | Path, old_sha: str) -> int:
    """Number of commits on HEAD since *old_sha* (0 if unknown). Powers staleness reporting."""
    if not old_sha or not is_git_repo(root):
        return 0
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-list", "--count", f"{old_sha}..HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return 0
    if proc.returncode != 0:
        return 0
    try:
        return int(proc.stdout.strip())
    except ValueError:  # pragma: no cover
        return 0


def collect_commits(root: str | Path, limit: int = 400) -> list[dict[str, Any]]:
    """Return recent commits with the files each touched. Empty list if git is unavailable."""
    if not is_git_repo(root):
        return []
    fmt = _REC + _UNIT.join(["%H", "%an", "%ae", "%aI", "%s"])
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "log", f"--pretty=format:{fmt}", "--name-only",
             "--no-merges", f"-n{limit}"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - env dependent
        return []
    if proc.returncode != 0:
        return []

    commits: list[dict[str, Any]] = []
    for record in proc.stdout.split(_REC):
        record = record.strip("\n")
        if not record:
            continue
        head, _, rest = record.partition("\n")
        fields = head.split(_UNIT)
        if len(fields) < 5:
            continue
        files = [ln.strip() for ln in rest.splitlines() if ln.strip()]
        commits.append({
            "sha": fields[0], "author_name": fields[1], "author_email": fields[2],
            "ts": fields[3], "message": fields[4], "files": files,
        })
    return commits


def compute_churn(commits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate per-file churn (commit count, authors, last change) from commits."""
    churn: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"commits": 0, "authors": set(), "last_ts": ""}
    )
    for commit in commits:
        for path in commit["files"]:
            entry = churn[path]
            entry["commits"] += 1
            entry["authors"].add(commit["author_email"])
            if commit["ts"] > entry["last_ts"]:
                entry["last_ts"] = commit["ts"]
    return {
        path: {"commits": e["commits"], "authors": sorted(e["authors"]), "last_ts": e["last_ts"]}
        for path, e in churn.items()
    }


__all__ = ["is_git_repo", "head_sha", "commits_since", "collect_commits", "compute_churn"]
