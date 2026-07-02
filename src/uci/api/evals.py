"""Bridge from the dashboard to UCI's own evaluation suite (``evals/``).

The eval suite is part of the UCI project, not of an arbitrary indexed repo — so these helpers locate
it relative to the installed package and degrade gracefully (the Evals tab hides) when it is absent.
The runner is invoked as a **subprocess with a fixed argv** and a **dataset allowlist**: no shell, no
free-form arguments, so exposing "run evals" over localhost adds no command-injection surface.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# source-checkout layout: src/uci/api/evals.py -> parents[3] == repo root that holds evals/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_EVALS = _REPO_ROOT / "evals"


def evals_dir() -> Path | None:
    """The eval suite directory, or ``None`` when this workspace has no runnable suite."""
    return _EVALS if (_EVALS / "run_eval.py").exists() else None


def dataset_names() -> list[str]:
    directory = evals_dir()
    if directory is None:
        return []
    names: list[str] = []
    for path in sorted((directory / "datasets").glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if "track" in obj and obj.get("name"):
            names.append(obj["name"])
    return names


def list_reports() -> list[dict]:
    """Newest-first summary of every report in ``evals/reports/`` (incl. the committed baseline)."""
    directory = evals_dir()
    if directory is None:
        return []
    reports_dir = directory / "reports"
    out: list[dict] = []
    for path in sorted(reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "name": path.stem,
            "run": obj.get("run"),
            "git_sha": obj.get("git_sha"),
            "baseline": path.name == "baseline.json",
            "tracks": {t: v.get("score") for t, v in obj.get("tracks", {}).items()},
        })
    return out


def load_report(name: str) -> dict | None:
    """Full report JSON by file stem; path-traversal safe."""
    directory = evals_dir()
    if directory is None:
        return None
    reports_dir = directory / "reports"
    path = reports_dir / f"{Path(name).name}.json"
    if not path.exists() or path.parent != reports_dir:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def build_command(dataset: str | None, baseline: bool) -> list[str]:
    """Fixed argv for the runner. Raises ``ValueError`` if ``dataset`` is not in the allowlist."""
    directory = evals_dir()
    if directory is None:
        raise ValueError("eval suite not available in this workspace")
    # NB: no --clean — a UI run must not wipe the .uci index of a registered project it evaluates
    cmd = [sys.executable, str(directory / "run_eval.py"), "-v"]
    if dataset:
        if dataset not in dataset_names():
            raise ValueError(f"unknown dataset: {dataset}")
        cmd += ["--dataset", dataset]
    if baseline:
        base = directory / "reports" / "baseline.json"
        if base.exists():
            cmd += ["--baseline", str(base)]
    return cmd


def run_eval_job(job, dataset: str | None, baseline: bool) -> dict:
    """Job target: stream ``run_eval.py`` stdout into ``job.log`` and summarise the outcome."""
    directory = evals_dir()
    if directory is None:
        raise RuntimeError("eval suite not available in this workspace")
    cmd = build_command(dataset, baseline)
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")}
    scope = dataset or "all datasets"
    job.log.append(f"running eval suite ({scope}, baseline={'on' if baseline else 'off'}) …")
    proc = subprocess.Popen(
        cmd, cwd=str(_REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        job.log.append(line.rstrip("\n"))
    code = proc.wait()
    reports = list_reports()
    return {
        "exit_code": code,
        "gate_passed": code == 0,
        "newest_report": reports[0] if reports else None,
    }


# --------------------------------------------------------------------------- dataset authoring
def _safe_stem(name: str) -> str:
    stem = "".join(c for c in (name or "") if c.isalnum() or c in "-_")
    return stem or "dataset"


def dataset_path(name: str) -> Path | None:
    directory = evals_dir()
    if directory is None:
        return None
    ddir = directory / "datasets"
    path = ddir / f"{_safe_stem(name)}.json"
    return path if path.parent == ddir else None


def _versions_dir(stem: str) -> Path | None:
    directory = evals_dir()
    if directory is None:
        return None
    return directory / "datasets" / ".versions" / _safe_stem(stem)


def read_dataset(name: str) -> dict | None:
    path = dataset_path(name)
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_dataset(name: str, content: dict) -> str:
    """Validate + persist a golden dataset, assigning a new **version** and archiving history.

    Every save bumps ``version`` and writes an immutable copy under
    ``evals/datasets/.versions/<stem>/`` so the full change history is queryable.
    """
    if not isinstance(content, dict) or "categories" not in content:
        raise ValueError("dataset must be an object with a 'categories' field")
    if not isinstance(content.get("categories"), dict):
        raise ValueError("'categories' must be an object")
    path = dataset_path(name)
    if path is None:
        raise ValueError("eval suite not available in this workspace")
    stem = _safe_stem(name)

    prev_version = 0
    if path.exists():
        try:
            prev_version = int(json.loads(path.read_text(encoding="utf-8")).get("version", 0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            prev_version = 0

    content = {k: v for k, v in content.items() if k not in ("version", "updated_at")}
    content.setdefault("name", stem)
    content.setdefault("track", "custom")
    content["version"] = prev_version + 1
    content["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    serialized = json.dumps(content, indent=2) + "\n"
    path.write_text(serialized, encoding="utf-8")
    vdir = _versions_dir(stem)
    if vdir is not None:
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / f"v{content['version']:04d}.json").write_text(serialized, encoding="utf-8")
    return stem


def list_versions(name: str) -> list[dict]:
    """History for a dataset, newest first: ``[{version, updated_at}]``."""
    vdir = _versions_dir(name)
    if vdir is None or not vdir.exists():
        return []
    out: list[dict] = []
    for path in sorted(vdir.glob("v*.json"), reverse=True):
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"version": content.get("version"), "updated_at": content.get("updated_at")})
    return out


def read_version(name: str, version: int) -> dict | None:
    vdir = _versions_dir(name)
    if vdir is None:
        return None
    path = vdir / f"v{int(version):04d}.json"
    if not path.exists() or path.parent != vdir:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def restore_version(name: str, version: int) -> str:
    """Restore an earlier version as a *new* version (history is append-only)."""
    content = read_version(name, version)
    if content is None:
        raise ValueError(f"no version {version} for {name}")
    return write_dataset(name, content)


def create_dataset(engine, repo_path: str, name: str, *, max_calls: int = 30, max_symbols: int = 12) -> dict:
    """Snapshot UCI's current extraction of ``engine`` into a golden dataset (regression baseline)."""
    from ..core.relationships import RESOLVED_LEVELS, RelationType

    overview = engine.overview()
    key = [k for k in overview.get("key_symbols", []) if k.get("path")][:max_symbols]
    symbol_lookup = [{"name": k["name"], "path": k["path"]} for k in key]

    calls: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rel in engine.graph.relationships(RelationType.CALLS):
        if rel.attributes.get("resolution") not in RESOLVED_LEVELS:
            continue
        src = engine.graph.get_entity(rel.src_id)
        dst = engine.graph.get_entity(rel.dst_id)
        if src is None or dst is None or dst.attributes.get("missing") or dst.attributes.get("external"):
            continue
        pair = (src.qualified_name, dst.qualified_name)
        if pair in seen:
            continue
        seen.add(pair)
        calls.append({"from": src.qualified_name, "to": dst.qualified_name,
                      "class": "internal", "expect_resolved": True})
        if len(calls) >= max_calls:
            break

    queries = [{"q": k["name"], "expected": [k["qualified_name"]]}
               for k in key[:6] if k.get("qualified_name")]

    impact: list[dict] = []
    for k in key[:4]:
        if not k.get("callers"):
            continue
        imp = engine.impact(k["name"])
        if not imp.get("ok"):
            continue
        callers = [h["qualified_name"] for h in imp["callers"]["resolved"] + imp["callers"]["candidates"]]
        impact.append({
            "symbol": k["name"],
            "callers": callers,
            "tests": [h["qualified_name"] for h in imp.get("tests", [])],
            "config": [h.get("name") for h in imp.get("config", [])],
        })

    return {
        "name": _safe_stem(name),
        "track": "custom",
        "repo": str(repo_path),
        "notes": f"Auto-generated regression snapshot of UCI's extraction of {repo_path}. Edit to curate.",
        "categories": {
            "symbol_lookup": symbol_lookup,
            "calls": calls,
            "queries": queries,
            "impact": impact,
        },
    }


__all__ = [
    "evals_dir",
    "dataset_names",
    "list_reports",
    "load_report",
    "build_command",
    "run_eval_job",
    "dataset_path",
    "read_dataset",
    "write_dataset",
    "create_dataset",
    "list_versions",
    "read_version",
    "restore_version",
]
