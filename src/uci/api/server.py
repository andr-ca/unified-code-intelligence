"""Dashboard + REST API on the Python standard-library HTTP server (zero runtime dependencies).

Single-threaded by design (fine for a local dashboard) so the shared SQLite connection needs no
cross-thread coordination. Serves both JSON (`/api/*`) and the server-rendered dashboard.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..engine import Engine
from ..mcp.tools import dispatch as mcp_dispatch
from ..mcp.tools import list_tools
from . import views

_STATIC = Path(__file__).parent / "static"
_STATIC_TYPES = {".css": "text/css", ".js": "application/javascript"}


def make_handler(engine: Engine):
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
                if path.startswith("/api/"):
                    return self._api_get(path, q)
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
            if path == "/":
                return self._html(views.overview_page(engine.overview()))
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
                name = payload.get("name", "")
                arguments = payload.get("arguments", {})
                return self._json(mcp_dispatch(engine, name, arguments))
            self._json({"ok": False, "error": {"code": "not_found", "message": parsed.path}}, 404)

    return Handler


def _module_root(engine: Engine, module_qname: str) -> tuple[str, str]:
    from ..core.entities import EntityType

    for entity in engine.graph.entities(kind=EntityType.MODULE, repo_id=engine.repo_id):
        if entity.qualified_name == module_qname:
            return entity.id, entity.name
    return "", ""


def serve(engine: Engine, host: str = "127.0.0.1", port: int = 8765) -> None:  # pragma: no cover - I/O
    httpd = HTTPServer((host, port), make_handler(engine))
    print(f"UCI dashboard on http://{host}:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


__all__ = ["serve", "make_handler"]
