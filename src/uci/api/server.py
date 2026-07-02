"""Dashboard + REST API on the Python standard-library HTTP server (zero runtime dependencies).

Single-threaded by design (fine for a local dashboard) so the shared SQLite connection needs no
cross-thread coordination. Serves both JSON (`/api/*`) and the server-rendered dashboard.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..engine import Engine
from ..mcp.tools import dispatch as mcp_dispatch
from ..mcp.tools import list_tools
from . import evals as evals_mod
from . import views
from .jobs import JobRunner
from .projects import ProjectManager, from_engine

_STATIC = Path(__file__).parent / "static"
_STATIC_TYPES = {".css": "text/css", ".js": "application/javascript"}


def make_handler(target, jobs: JobRunner | None = None):
    manager = target if isinstance(target, ProjectManager) else from_engine(target)
    jobs = jobs or JobRunner()
    views.configure(show_evals=evals_mod.evals_dir() is not None)

    class Handler(BaseHTTPRequestHandler):
        server_version = "UCI/0.1"

        def log_message(self, *args) -> None:  # keep the console quiet
            pass

        # -- response helpers ------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _html(self, markup: str, code: int = 200) -> None:
            self._send(code, markup.encode("utf-8"), "text/html; charset=utf-8")

        # -- GET -------------------------------------------------------------
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                if path.startswith("/static/"):
                    return self._static(path)
                # lock-free routes stay responsive while a build holds the active-project lock
                if path == "/api/jobs" or path.startswith("/api/jobs/"):
                    return self._jobs_get(path)
                if path == "/api/projects":
                    return self._json({"ok": True, "projects": manager.list(), "active": manager.active_name})
                if path.startswith("/api/evals/"):
                    return self._evals_get(path, q)
                if not path.startswith("/api/"):
                    views.set_project_context(manager.summary(), manager.active_name)
                if path == "/evals":
                    return self._evals_page()
                if path == "/projects":
                    return self._html(views.projects_page(manager.list(), manager.active_name))
                if manager.active_name is None:
                    if path.startswith("/api/"):
                        return self._json({"ok": False, "error": {"code": "no_project"}}, 409)
                    return self._html(views.no_projects_page())
                lock = manager.active_lock()
                if path.startswith("/api/"):
                    with lock:
                        return self._api_get(path, q)
                with lock:
                    return self._page(path, q)
            except BrokenPipeError:  # pragma: no cover
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._json({"ok": False, "error": {"code": "internal", "message": str(exc)}}, 500)

        def _static(self, path: str) -> None:
            name = Path(path).name
            file = _STATIC / name
            if not file.exists() or file.parent != _STATIC:
                return self._send(404, b"not found", "text/plain")
            ctype = _STATIC_TYPES.get(file.suffix, "application/octet-stream")
            self._send(200, file.read_bytes(), ctype)

        def _api_get(self, path: str, q: dict) -> None:
            engine = manager.active()
            if path == "/api/overview":
                return self._json(engine.overview())
            if path == "/api/architecture":
                return self._json(engine.architecture())
            if path == "/api/onboarding":
                return self._json(engine.onboarding())
            if path == "/api/search":
                return self._json(engine.search(q.get("q", ""), top_k=int(q.get("k", 10))))
            if path == "/api/symbol":
                return self._json(engine.find_symbol(q.get("name", ""), exact=q.get("exact", "1") == "1"))
            if path == "/api/impact":
                return self._json(engine.impact(q.get("q", "")))
            if path == "/api/edit_context":
                return self._json(engine.edit_context(q.get("q", "")))
            if path == "/api/callers":
                return self._json(engine.callers(q.get("symbol", ""), depth=int(q.get("depth", 1))))
            if path == "/api/callees":
                return self._json(engine.callees(q.get("symbol", ""), depth=int(q.get("depth", 1))))
            if path == "/api/module":
                return self._json(engine.explain_module(q.get("q", "")))
            if path == "/api/graph":
                return self._json(engine.graph_neighborhood(q.get("id", ""), depth=int(q.get("depth", 1))))
            if path == "/api/entity":
                return self._json(engine.entity_detail(q.get("id", "")))
            if path == "/api/mcp/tools":
                return self._json({"tools": list_tools(engine)})
            if path == "/api/gaps":
                return self._json(engine.gaps(q.get("kind")))
            return self._json({"ok": False, "error": {"code": "not_found", "message": path}}, 404)

        def _page(self, path: str, q: dict) -> None:
            engine = manager.active()
            # a registered-but-unindexed project (e.g. its .uci was cleaned) gets a build prompt,
            # not a broken/empty view — except /build itself, which does the indexing
            if path != "/build" and not engine.is_indexed():
                return self._html(views.unindexed_page(manager.active_name))
            if path == "/":
                return self._html(views.overview_page(engine.overview()))
            if path == "/build":
                return self._html(views.build_page(
                    engine.overview().get("name"), engine._index_status(),
                    engine.capabilities(), jobs.active("build")))
            if path == "/search":
                query = q.get("q", "")
                results = engine.search(query, top_k=int(q.get("k", 15)))["results"] if query else []
                return self._html(views.search_page(query, results))
            if path == "/graph":
                root_id = q.get("id")
                if not root_id:
                    root_id, label = engine.default_graph_root()
                else:
                    ent = engine.graph.get_entity(root_id)
                    label = ent.name if ent else root_id
                return self._html(views.graph_page(root_id, label))
            if path == "/architecture":
                return self._html(views.architecture_page(engine.architecture()))
            if path == "/onboarding":
                return self._html(views.onboarding_page(engine.onboarding()))
            if path == "/gaps":
                return self._html(views.gaps_page(engine.gaps(q.get("kind"))))
            if path == "/module":
                data = engine.explain_module(q.get("q", ""))
                if data.get("ok"):
                    root_id, _ = _module_root(engine, data["module"])
                    data["root_id"] = root_id
                return self._html(views.module_page(data))
            if path == "/impact":
                query = q.get("q", "")
                data = engine.impact(query) if query else {"ok": False}
                return self._html(views.impact_page(query, data))
            if path == "/symbol":
                detail = engine.entity_detail(q.get("id", ""))
                if not detail.get("ok"):
                    return self._html(views.layout("Symbol", "/", "<div class='container'><p class='muted'>Not found.</p></div>"), 404)
                return self._html(views.symbol_page(
                    detail["entity"], detail["callers"], detail["callees"], detail["source"]))
            return self._html(views.layout("Not found", "/", "<div class='container'><h1>404</h1></div>"), 404)

        # -- POST ------------------------------------------------------------
        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"ok": False, "error": {"code": "bad_json"}}, 400)
            if parsed.path == "/api/mcp/call":
                if manager.active_name is None:
                    return self._json({"ok": False, "error": {"code": "no_project"}}, 409)
                with manager.active_lock():
                    return self._json(mcp_dispatch(manager.active(), payload.get("name", ""), payload.get("arguments", {})))
            if parsed.path == "/api/build":
                return self._start_build(payload.get("name") or None, bool(payload.get("full", True)))
            if parsed.path == "/api/evals/run":
                return self._start_eval(payload.get("dataset") or None, bool(payload.get("baseline", False)))
            if parsed.path == "/api/evals/create":
                return self._create_eval(payload.get("project", ""), payload.get("name", ""))
            if parsed.path == "/api/evals/dataset":
                return self._save_dataset(payload.get("name", ""), payload.get("content"))
            if parsed.path == "/api/evals/restore":
                return self._restore_dataset(payload.get("name", ""), payload.get("version"))
            if parsed.path == "/api/projects":
                return self._add_project(payload.get("path", ""), payload.get("name") or None)
            if parsed.path == "/api/projects/activate":
                return self._activate_project(payload.get("name", ""))
            if parsed.path == "/api/projects/remove":
                return self._remove_project(payload.get("name", ""))
            self._json({"ok": False, "error": {"code": "not_found", "message": parsed.path}}, 404)

        # -- ops: jobs / build / evals --------------------------------------
        def _jobs_get(self, path: str) -> None:
            if path == "/api/jobs":
                return self._json({"ok": True, "jobs": [j.to_dict() for j in jobs.recent()]})
            job = jobs.get(path.rsplit("/", 1)[-1])
            if job is None:
                return self._json({"ok": False, "error": {"code": "not_found"}}, 404)
            return self._json({"ok": True, "job": job.to_dict()})

        def _evals_get(self, path: str, q: dict) -> None:
            if path == "/api/evals/reports":
                active = jobs.active("eval")
                return self._json({
                    "ok": True,
                    "available": evals_mod.evals_dir() is not None,
                    "datasets": evals_mod.dataset_names(),
                    "reports": evals_mod.list_reports(),
                    "active": active.to_dict() if active else None,
                })
            if path == "/api/evals/report":
                report = evals_mod.load_report(q.get("run", ""))
                if report is None:
                    return self._json({"ok": False, "error": {"code": "not_found"}}, 404)
                return self._json({"ok": True, "report": report})
            if path == "/api/evals/dataset":
                dataset = evals_mod.read_dataset(q.get("name", ""))
                if dataset is None:
                    return self._json({"ok": False, "error": {"code": "not_found"}}, 404)
                return self._json({"ok": True, "dataset": dataset})
            if path == "/api/evals/versions":
                return self._json({"ok": True, "versions": evals_mod.list_versions(q.get("name", ""))})
            if path == "/api/evals/version":
                content = evals_mod.read_version(q.get("name", ""), int(q.get("version", 0) or 0))
                if content is None:
                    return self._json({"ok": False, "error": {"code": "not_found"}}, 404)
                return self._json({"ok": True, "dataset": content})
            return self._json({"ok": False, "error": {"code": "not_found", "message": path}}, 404)

        def _start_build(self, name, full: bool) -> None:
            proj = name or manager.active_name
            if proj is None or proj not in {p["name"] for p in manager.summary()}:
                return self._json({"ok": False, "error": {"code": "no_project", "message": "unknown project"}}, 409)

            def target(job):
                eng = manager.engine_for(proj)
                lock = manager.lock_for(proj)
                job.log.append(f"indexing '{proj}' (full={full}) …")
                with lock:
                    stats = eng.index(full=full)
                    status = eng._index_status()
                data = stats.to_dict()
                job.log.append(
                    f"done: {data['entities']} entities, {data['relationships']} relationships, "
                    f"{data['files_scanned']} files, {data['gaps']} gaps ({data['elapsed_ms']} ms)")
                return {"project": proj, "stats": data, "index": status}

            job, err = jobs.start("build", target, label=f"Index {proj}")
            if err:
                return self._json({"ok": False, "error": {"code": "busy", "message": err}}, 409)
            return self._json({"ok": True, "job": job.to_dict()})

        def _add_project(self, path: str, name) -> None:
            if not path:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": "path required"}}, 400)
            try:
                rec = manager.add(path, name)
            except ValueError as exc:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": str(exc)}}, 400)
            return self._json({"ok": True, "project": rec, "active": manager.active_name})

        def _activate_project(self, name: str) -> None:
            if not manager.set_active(name):
                return self._json({"ok": False, "error": {"code": "not_found", "message": name}}, 404)
            return self._json({"ok": True, "active": manager.active_name})

        def _remove_project(self, name: str) -> None:
            if not manager.remove(name):
                return self._json({"ok": False, "error": {"code": "not_found", "message": name}}, 404)
            return self._json({"ok": True, "active": manager.active_name})

        def _start_eval(self, dataset, baseline: bool) -> None:
            if evals_mod.evals_dir() is None:
                return self._json({"ok": False, "error": {
                    "code": "unavailable", "message": "eval suite not present in this workspace"}}, 404)
            try:
                evals_mod.build_command(dataset, baseline)  # validate dataset allowlist up front
            except ValueError as exc:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": str(exc)}}, 400)
            label = f"Eval: {dataset or 'all datasets'}"
            job, err = jobs.start("eval", lambda job: evals_mod.run_eval_job(job, dataset, baseline), label=label)
            if err:
                return self._json({"ok": False, "error": {"code": "busy", "message": err}}, 409)
            return self._json({"ok": True, "job": job.to_dict()})

        def _evals_page(self) -> None:
            if evals_mod.evals_dir() is None:
                return self._html(views.evals_unavailable_page())
            return self._html(views.evals_page(
                evals_mod.list_reports(), evals_mod.dataset_names(), manager.summary()))

        def _create_eval(self, project: str, name: str) -> None:
            if evals_mod.evals_dir() is None:
                return self._json({"ok": False, "error": {"code": "unavailable"}}, 404)
            if not name:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": "name required"}}, 400)
            if project not in {p["name"] for p in manager.summary()}:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": "unknown project"}}, 400)
            engine = manager.engine_for(project)
            if not engine.is_indexed():
                return self._json({"ok": False, "error": {
                    "code": "not_indexed", "message": "index the project first"}}, 409)
            with manager.lock_for(project):
                content = evals_mod.create_dataset(engine, manager.path_of(project), name)
            try:
                stem = evals_mod.write_dataset(name, content)
            except ValueError as exc:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": str(exc)}}, 400)
            return self._json({"ok": True, "name": stem, "dataset": content})

        def _save_dataset(self, name: str, content) -> None:
            if evals_mod.evals_dir() is None:
                return self._json({"ok": False, "error": {"code": "unavailable"}}, 404)
            try:
                stem = evals_mod.write_dataset(name, content)
            except (ValueError, TypeError) as exc:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": str(exc)}}, 400)
            return self._json({"ok": True, "name": stem, "versions": evals_mod.list_versions(stem)})

        def _restore_dataset(self, name: str, version) -> None:
            if evals_mod.evals_dir() is None:
                return self._json({"ok": False, "error": {"code": "unavailable"}}, 404)
            try:
                stem = evals_mod.restore_version(name, int(version))
            except (ValueError, TypeError) as exc:
                return self._json({"ok": False, "error": {"code": "bad_request", "message": str(exc)}}, 400)
            return self._json({"ok": True, "name": stem, "versions": evals_mod.list_versions(stem)})

    return Handler


def _module_root(engine: Engine, module_qname: str) -> tuple[str, str]:
    from ..core.entities import EntityType

    for entity in engine.graph.entities(kind=EntityType.MODULE, repo_id=engine.repo_id):
        if entity.qualified_name == module_qname:
            return entity.id, entity.name
    return "", ""


def serve(target, host: str = "127.0.0.1", port: int = 8765) -> None:  # pragma: no cover - I/O
    httpd = ThreadingHTTPServer((host, port), make_handler(target))
    print(f"UCI dashboard on http://{host}:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


__all__ = ["serve", "make_handler"]
