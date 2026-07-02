"""Multi-project registry for the dashboard.

Each project is a code repo indexed into its **own** database (``<path>/.uci/uci.db``) — no shared
tables, no cross-project bleed. This manager keeps a small JSON registry under ``UCI_HOME`` (default
``~/.uci``, env-overridable) and lazily opens/caches one :class:`Engine` + lock per project so the UI
can switch the active project at runtime.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from ..config import Config
from ..engine import Engine


def _home() -> Path:
    return Path(os.environ.get("UCI_HOME", str(Path.home() / ".uci")))


def registry_path() -> Path:
    override = os.environ.get("UCI_PROJECTS_FILE")
    return Path(override) if override else _home() / "projects.json"


class ProjectManager:
    """Registry + lazy engine cache for multiple projects, one DB each."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or registry_path()
        self._lock = threading.Lock()
        self._engines: dict[str, Engine] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._projects: dict[str, dict] = {}
        self._active: str | None = None
        self._load()

    # -- persistence --------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for rec in data.get("projects", []):
            if rec.get("name") and rec.get("path"):
                self._projects[rec["name"]] = {
                    "name": rec["name"], "path": rec["path"], "added_at": rec.get("added_at", 0)}
        active = data.get("active")
        self._active = active if active in self._projects else (next(iter(self._projects), None))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"active": self._active, "projects": list(self._projects.values())}, indent=2) + "\n",
            encoding="utf-8")

    def _unique_name(self, base: str) -> str:
        name, i = base, 2
        while name in self._projects:
            name, i = f"{base}-{i}", i + 1
        return name

    # -- mutations ----------------------------------------------------------
    def add(self, path: str, name: str | None = None, activate: bool = True) -> dict:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"not a directory: {resolved}")
        with self._lock:
            for rec in self._projects.values():
                if Path(rec["path"]) == resolved:
                    if activate:
                        self._active = rec["name"]
                        self._save()
                    return rec
            nm = self._unique_name(name or resolved.name or "repo")
            rec = {"name": nm, "path": str(resolved), "added_at": int(time.time())}
            self._projects[nm] = rec
            if activate or self._active is None:
                self._active = nm
            self._save()
            return rec

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self._projects:
                return False
            self._projects.pop(name)
            engine = self._engines.pop(name, None)
            self._locks.pop(name, None)
            if engine is not None:
                engine.close()
            if self._active == name:
                self._active = next(iter(self._projects), None)
            self._save()
            return True

    def set_active(self, name: str) -> bool:
        with self._lock:
            if name not in self._projects:
                return False
            self._active = name
            self._save()
            return True

    # -- engine access ------------------------------------------------------
    def engine_for(self, name: str) -> Engine:
        with self._lock:
            if name not in self._projects:
                raise KeyError(name)
            engine = self._engines.get(name)
            if engine is None:
                engine = Engine(Config.from_env(self._projects[name]["path"]))
                self._engines[name] = engine
                self._locks.setdefault(name, threading.Lock())
            return engine

    def lock_for(self, name: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(name, threading.Lock())

    @property
    def active_name(self) -> str | None:
        return self._active

    def active(self) -> Engine:
        if self._active is None:
            raise RuntimeError("no active project registered")
        return self.engine_for(self._active)

    def active_lock(self) -> threading.Lock:
        if self._active is None:
            raise RuntimeError("no active project registered")
        return self.lock_for(self._active)

    def has_projects(self) -> bool:
        return bool(self._projects)

    def summary(self) -> list[dict]:
        """Cheap name/active list for the top-bar switcher (opens no engines)."""
        return [{"name": name, "active": name == self._active} for name in self._projects]

    def path_of(self, name: str) -> str | None:
        rec = self._projects.get(name)
        return rec["path"] if rec else None

    # -- listing ------------------------------------------------------------
    def list(self) -> list[dict]:
        out: list[dict] = []
        for name, rec in self._projects.items():
            info = {"name": name, "path": rec["path"], "active": name == self._active,
                    "indexed": False, "entities": 0, "files": 0}
            try:
                engine = self.engine_for(name)
                with self.lock_for(name):
                    if engine.is_indexed():
                        counts = engine.overview().get("counts", {})
                        info["indexed"] = True
                        info["entities"] = sum(v for v in counts.values() if isinstance(v, int))
                        info["files"] = counts.get("file", 0)
            except Exception:  # a broken project must not break the listing
                pass
            out.append(info)
        return out

    def close(self) -> None:
        for engine in self._engines.values():
            engine.close()


def from_engine(engine: Engine) -> ProjectManager:
    """Wrap one already-open Engine as a single-project manager (back-compat for ``make_handler``).

    Does not read or write the on-disk registry — safe for tests and embedded single-project use.
    """
    mgr = ProjectManager.__new__(ProjectManager)
    mgr._path = registry_path()
    mgr._lock = threading.Lock()
    name = engine.config.repo_path.name or "repo"
    mgr._engines = {name: engine}
    mgr._locks = {name: threading.Lock()}
    mgr._projects = {name: {"name": name, "path": str(engine.config.repo_path), "added_at": int(time.time())}}
    mgr._active = name
    return mgr


__all__ = ["ProjectManager", "from_engine", "registry_path"]
