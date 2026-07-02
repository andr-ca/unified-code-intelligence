"""API/dashboard tests: run the stdlib HTTP server on an ephemeral port and hit real endpoints."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from uci.api.server import make_handler


@pytest.fixture
def server(indexed_engine):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(indexed_engine))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, indexed_engine
    httpd.shutdown()
    httpd.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def _get_status(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _poll(base, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw, _ = _get(base, "/api/jobs/" + job_id)
        job = json.loads(raw)["job"]
        if job["state"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish in time")


def test_api_overview_json(server):
    base, _ = server
    raw, ctype = _get(base, "/api/overview")
    data = json.loads(raw)
    assert "application/json" in ctype
    assert data["totals"]["classes"] >= 3


def test_dashboard_html(server):
    base, _ = server
    raw, ctype = _get(base, "/")
    assert "text/html" in ctype
    assert b"Unified Code Intelligence" in raw and b"overview" in raw.lower()


def test_api_graph_neighborhood(server):
    base, engine = server
    root, _ = engine.default_graph_root()
    raw, _ = _get(base, "/api/graph?id=" + urllib.parse.quote(root) + "&depth=2")
    data = json.loads(raw)
    assert data["ok"] and data["nodes"]


def test_api_impact(server):
    base, _ = server
    raw, _ = _get(base, "/api/impact?q=" + urllib.parse.quote("PricingCalculator.calculate"))
    data = json.loads(raw)
    assert data["ok"] and data["callers"]["resolved"]


def test_api_mcp_tools(server):
    base, _ = server
    from uci.mcp.tools import TOOL_SPECS
    raw, _ = _get(base, "/api/mcp/tools")
    tools = json.loads(raw)["tools"]
    names = {t["name"] for t in tools}
    assert "search_code" in names and "list_index_gaps" in names
    assert len(tools) == len(TOOL_SPECS)  # count from source of truth, not a literal


def test_api_mcp_call_post(server):
    base, _ = server
    body = json.dumps({"name": "find_symbol", "arguments": {"name": "PricingCalculator"}}).encode()
    req = urllib.request.Request(base + "/api/mcp/call", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    assert data["ok"] and data["results"]


def test_static_assets(server):
    base, _ = server
    css, ctype = _get(base, "/static/app.css")
    assert b":root" in css and "css" in ctype
    js, _ = _get(base, "/static/app.js")
    assert b"initGraph" in js and b"initBuild" in js and b"initEvals" in js


# --------------------------------------------------------------------------- ops: build
def test_api_build_job_reindexes(server):
    base, _ = server
    status, data = _post(base, "/api/build", {"full": True})
    assert status == 200 and data["ok"], data
    job = _poll(base, data["job"]["id"])
    assert job["state"] == "done", job
    assert job["result"]["stats"]["entities"] >= 1
    assert "index" in job["result"]
    # overview still answers after a live rebuild
    raw, _ = _get(base, "/api/overview")
    assert json.loads(raw)["totals"]["classes"] >= 3


def test_api_jobs_list_and_missing(server):
    base, _ = server
    _post(base, "/api/build", {"full": False})
    raw, _ = _get(base, "/api/jobs")
    jobs = json.loads(raw)["jobs"]
    assert jobs and jobs[0]["kind"] == "build"
    status, data = _get_status(base, "/api/jobs/does-not-exist")
    assert status == 404 and not data["ok"]


def test_build_page_html(server):
    base, _ = server
    raw, _ = _get(base, "/build")
    assert b"Build" in raw and b"Index status" in raw and b'data-build="full"' in raw


# --------------------------------------------------------------------------- ops: evals
def _evals_present() -> bool:
    from uci.api import evals
    return evals.evals_dir() is not None


@pytest.mark.skipif(not _evals_present(), reason="eval suite not in this workspace")
def test_api_evals_reports_and_report(server):
    base, _ = server
    raw, _ = _get(base, "/api/evals/reports")
    data = json.loads(raw)
    assert data["ok"] and data["available"]
    assert "shop" in data["datasets"]
    names = {r["name"] for r in data["reports"]}
    assert "baseline" in names
    raw, _ = _get(base, "/api/evals/report?run=baseline")
    report = json.loads(raw)["report"]
    assert "supported" in report["tracks"]
    assert report["tracks"]["supported"]["datasets"]["shop"]["score"] >= 90.0


@pytest.mark.skipif(not _evals_present(), reason="eval suite not in this workspace")
def test_api_evals_run_rejects_unknown_dataset(server):
    base, _ = server
    status, data = _post(base, "/api/evals/run", {"dataset": "__nope__"})
    assert status == 400 and not data["ok"]
    # no job was created for the rejected request
    raw, _ = _get(base, "/api/jobs")
    assert all(j["kind"] != "eval" for j in json.loads(raw)["jobs"])


@pytest.mark.skipif(not _evals_present(), reason="eval suite not in this workspace")
def test_api_evals_run_shop_end_to_end(server):
    base, _ = server
    from uci.api import evals
    reports_dir = evals.evals_dir() / "reports"
    before = {p.name for p in reports_dir.glob("run-*")}
    try:
        status, data = _post(base, "/api/evals/run", {"dataset": "shop"})
        assert status == 200 and data["ok"], data
        job = _poll(base, data["job"]["id"], timeout=180)
        assert job["state"] == "done", job
        assert job["result"]["exit_code"] == 0
        assert any("shop" in line for line in job["log"])
    finally:
        for path in reports_dir.glob("run-*"):
            if path.name not in before:
                path.unlink()


@pytest.mark.skipif(not _evals_present(), reason="eval suite not in this workspace")
def test_evals_page_html_and_nav(server):
    base, _ = server
    raw, _ = _get(base, "/evals")
    assert b"Evaluations" in raw and b"eval-run" in raw
    # nav advertises both ops tabs when the suite is present
    home, _ = _get(base, "/")
    assert b'href="/build"' in home and b'href="/evals"' in home


# --------------------------------------------------------------------------- multi-project
@pytest.fixture
def project_server(indexed_engine, tmp_path):
    from uci.api.projects import ProjectManager
    mgr = ProjectManager(path=tmp_path / "projects.json")  # temp registry — never touches ~/.uci
    mgr.add(str(indexed_engine.config.repo_path), name="sample")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(mgr))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, mgr, tmp_path
    httpd.shutdown()
    httpd.server_close()
    mgr.close()


def test_projects_list_and_active(project_server):
    base, _, _ = project_server
    raw, _ = _get(base, "/api/projects")
    data = json.loads(raw)
    assert data["ok"] and data["active"] == "sample"
    sample = next(p for p in data["projects"] if p["name"] == "sample")
    assert sample["indexed"] and sample["entities"] > 0


def test_projects_add_activate_and_db_isolation(project_server):
    base, _, tmp_path = project_server
    proj2 = tmp_path / "proj2"
    proj2.mkdir()
    (proj2 / "m.py").write_text("def widget_fn():\n    return 1\n")
    status, data = _post(base, "/api/projects", {"path": str(proj2)})
    assert status == 200 and data["ok"], data

    _post(base, "/api/projects/activate", {"name": "proj2"})
    assert json.loads(_get(base, "/api/projects")[0])["active"] == "proj2"
    _, built = _post(base, "/api/build", {"name": "proj2"})
    job = _poll(base, built["job"]["id"])
    assert job["state"] == "done" and job["result"]["project"] == "proj2"

    # isolation: proj2's symbol lives only in proj2's DB, never in the sample project's graph
    raw, _ = _get(base, "/api/search?q=" + urllib.parse.quote("widget_fn"))
    assert any(h["qualified_name"].endswith("widget_fn") for h in json.loads(raw)["results"])
    _post(base, "/api/projects/activate", {"name": "sample"})
    raw, _ = _get(base, "/api/search?q=" + urllib.parse.quote("widget_fn"))
    assert all(not h["qualified_name"].endswith("widget_fn") for h in json.loads(raw)["results"])


def test_projects_add_rejects_bad_path(project_server):
    base, _, _ = project_server
    status, data = _post(base, "/api/projects", {"path": "/no/such/dir/xyzzy"})
    assert status == 400 and not data["ok"]


def test_unindexed_project_prompts_build(project_server):
    base, _, tmp_path = project_server
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "a.py").write_text("x = 1\n")
    _post(base, "/api/projects", {"path": str(fresh)})
    _post(base, "/api/projects/activate", {"name": "fresh"})
    # every data page redirects to the build prompt instead of a broken/empty view
    for page in ("/", "/graph", "/architecture"):
        raw, _ = _get(base, page)
        assert b"isn't indexed yet" in raw and b'data-build="full"' in raw, page


@pytest.mark.skipif(not _evals_present(), reason="eval suite not in this workspace")
def test_evals_create_and_edit_dataset(project_server):
    base, _, _ = project_server
    from uci.api import evals
    name = "pytest_sample_snapshot"
    path = evals.dataset_path(name)
    try:
        status, data = _post(base, "/api/evals/create", {"project": "sample", "name": name})
        assert status == 200 and data["ok"], data
        assert path.exists()
        cats = data["dataset"]["categories"]
        assert cats["symbol_lookup"] and cats["calls"]  # snapshot captured real facts
        assert name in json.loads(_get(base, "/api/evals/reports")[0])["datasets"]  # now runnable
        loaded = json.loads(_get(base, "/api/evals/dataset?name=" + name)[0])["dataset"]
        assert loaded["track"] == "custom"
        loaded["notes"] = "edited by test"
        status, saved = _post(base, "/api/evals/dataset", {"name": name, "content": loaded})
        assert status == 200 and saved["ok"]
        assert evals.read_dataset(name)["notes"] == "edited by test"
        status, bad = _post(base, "/api/evals/dataset", {"name": name, "content": {"no": "cats"}})
        assert status == 400 and not bad["ok"]
    finally:
        if path and path.exists():
            path.unlink()


@pytest.mark.skipif(not _evals_present(), reason="eval suite not in this workspace")
def test_evals_create_requires_indexed_project(project_server):
    base, _, tmp_path = project_server
    fresh = tmp_path / "unindexed"
    fresh.mkdir()
    (fresh / "z.py").write_text("y = 2\n")
    _post(base, "/api/projects", {"path": str(fresh)})  # registered but NOT indexed
    status, data = _post(base, "/api/evals/create", {"project": "unindexed", "name": "x"})
    assert status == 409 and data["error"]["code"] == "not_indexed"


def test_projects_remove_and_page(project_server):
    base, _, tmp_path = project_server
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "e.py").write_text("x = 1\n")
    _post(base, "/api/projects", {"path": str(extra)})
    status, data = _post(base, "/api/projects/remove", {"name": "extra"})
    assert status == 200 and data["ok"]
    assert "extra" not in {p["name"] for p in json.loads(_get(base, "/api/projects")[0])["projects"]}
    # projects page + top-bar switcher render
    raw, _ = _get(base, "/projects")
    assert b"Registered projects" in raw and b"project-table" in raw
    home, _ = _get(base, "/")
    assert b'id="project-switcher"' in home and b'href="/projects"' in home
