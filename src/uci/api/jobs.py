"""In-process background job runner for the dashboard (dependency-free).

Long operations (re-indexing, running the eval suite) must not block the HTTP handler, so they run
on a worker thread while the UI polls :meth:`JobRunner.get` for status + streamed log lines. Only one
job per *kind* runs at a time — this is what keeps concurrent index writes off the shared SQLite store.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    kind: str
    label: str = ""
    state: str = "running"  # running | done | failed
    started: float = field(default_factory=time.time)
    ended: float | None = None
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        end = self.ended if self.ended is not None else time.time()
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "started": self.started,
            "ended": self.ended,
            "elapsed_ms": int((end - self.started) * 1000),
            "log": list(self.log),
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    """Runs one job per kind at a time; keeps a bounded history for polling."""

    def __init__(self, history: int = 40) -> None:
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._active: dict[str, str] = {}  # kind -> currently-running job id
        self._lock = threading.Lock()
        self._history = history

    def active(self, kind: str) -> Job | None:
        with self._lock:
            jid = self._active.get(kind)
            job = self._jobs.get(jid) if jid else None
        return job if job and job.state == "running" else None

    def start(self, kind: str, target: Callable[[Job], dict | None], label: str = "") -> tuple[Job | None, str | None]:
        """Enqueue a job. Returns ``(job, None)`` or ``(None, reason)`` if that kind is already running."""
        with self._lock:
            current = self._active.get(kind)
            if current and self._jobs[current].state == "running":
                return None, f"a {kind} job is already running"
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._active[kind] = job.id
            self._evict_locked()
        threading.Thread(target=self._run, args=(job, target), daemon=True, name=f"uci-{kind}").start()
        return job, None

    def _run(self, job: Job, target: Callable[[Job], dict | None]) -> None:
        try:
            result = target(job)
            job.result = result if isinstance(result, dict) else None
            job.state = "done"
        except Exception as exc:  # capture — a job failure must never crash the server
            job.error = str(exc)
            job.log.append(f"ERROR: {exc}")
            job.log.extend(traceback.format_exc().splitlines()[-8:])
            job.state = "failed"
        finally:
            job.ended = time.time()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        with self._lock:
            ids = self._order[-limit:][::-1]
            return [self._jobs[i] for i in ids if i in self._jobs]

    def _evict_locked(self) -> None:
        while len(self._order) > self._history:
            oldest = self._order.pop(0)
            job = self._jobs.get(oldest)
            if job and job.state == "running":
                self._order.append(oldest)  # never evict a running job
                break
            self._jobs.pop(oldest, None)


__all__ = ["Job", "JobRunner"]
