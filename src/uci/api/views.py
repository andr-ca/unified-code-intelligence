"""Server-rendered dashboard views (dependency-free HTML). All dynamic text is HTML-escaped."""

from __future__ import annotations

import html
from urllib.parse import quote

_NAV_GROUPS: list[tuple[str, str | None, list[tuple[str, str]]]] = [
    ("Overview", "/", []),
    ("Understand", None, [
        ("/understand", "Guided Tour"),
        ("/architecture", "Architecture"),
        ("/flows", "Flows"),
        ("/onboarding", "Onboarding"),
        ("/docs", "Docs"),
    ]),
    ("Explore", None, [
        ("/search", "Search"),
        ("/graph", "Graph"),
    ]),
    ("Analyze", None, [
        ("/metrics", "Metrics"),
        ("/gaps", "Gaps"),
    ]),
    ("Data", None, [
        ("/build", "Build"),
        ("/enrich", "Enrich"),
        ("/db", "Database"),
    ]),
    ("Settings", None, [
        ("/projects", "Projects"),
        ("/config", "Config"),
    ]),
]

_SHOW_EVALS = False
_PROJECTS: list[dict] = []
_ACTIVE: str | None = None


def configure(show_evals: bool) -> None:
    """Toggle capabilities that depend on the served workspace (currently the Evals tab)."""
    global _SHOW_EVALS
    _SHOW_EVALS = show_evals


def set_project_context(projects: list[dict], active: str | None) -> None:
    """Feed the top-bar project switcher (called per page render)."""
    global _PROJECTS, _ACTIVE
    _PROJECTS, _ACTIVE = projects, active


def _nav_groups() -> list[tuple[str, str | None, list[tuple[str, str]]]]:
    """Nav groups for the top bar, injecting the Evals item into Data when the suite is present."""
    groups = [(label, href, list(children)) for label, href, children in _NAV_GROUPS]
    if _SHOW_EVALS:
        for label, _href, children in groups:
            if label == "Data":
                children.append(("/evals", "Evals"))
    return groups


def _render_nav(active: str) -> str:
    parts = []
    for label, href, children in _nav_groups():
        if not children and href is not None:
            cls = "active" if href == active else ""
            parts.append(f'<a href="{href}" class="{cls}">{_e(label)}</a>')
            continue
        group_active = any(h == active for h, _ in children)
        drop = "".join(
            f'<a href="{h}" class="{"active" if h == active else ""}">{_e(lbl)}</a>'
            for h, lbl in children)
        parts.append(
            f'<div class="menu{" active" if group_active else ""}">'
            f'<button type="button" class="menu-btn" aria-haspopup="true">'
            f'{_e(label)}<span class="caret">\u25be</span></button>'
            f'<div class="menu-drop">{drop}</div></div>')
    return "".join(parts)


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _switcher() -> str:
    if not _PROJECTS:
        return ""
    opts = "".join(
        f'<option value="{_e(p["name"])}"{" selected" if p.get("active") else ""}>{_e(p["name"])}</option>'
        for p in _PROJECTS
    )
    return f'<label class="proj-switch">project <select id="project-switcher">{opts}</select></label>'


def layout(title: str, active: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} · UCI</title>
<link rel="stylesheet" href="/static/app.css"></head>
<body>
<header class="topbar">
  <span class="brand"><b>UCI</b> · Unified Code Intelligence</span>
  <nav class="main">{_render_nav(active)}</nav>
  <span class="topbar-right">{_switcher()}</span>
</header>
{body}
<script src="/static/app.js"></script>
</body></html>"""


# --------------------------------------------------------------------------- ops pages
def build_page(repo_name, status: dict, caps: dict, active_job) -> str:
    running = active_job is not None
    status_rows = "".join(
        f"<tr><td>{_e(k)}</td><td class='mono'>{_e(v)}</td></tr>"
        for k, v in (
            ("generation", status.get("generation", 0)),
            ("head_sha", status.get("head_sha") or "—"),
            ("indexed_at", status.get("indexed_at") or "—"),
            ("commits_behind", status.get("commits_behind", 0)),
        )
    )
    active_tools = ", ".join(name for name, on in caps.items() if on) or "core only"
    disabled = "disabled" if running else ""
    body = f"""<div class="container">
  <h1>Build &amp; index</h1>
  <p class="sub">Re-index <b>{_e(repo_name or 'this repository')}</b> from the working tree.
  The graph is rebuilt every run; embeddings update incrementally for changed files only.</p>
  <div class="split">
    <div>
      <div class="card">
        <div class="card-h">Index status</div>
        <table class="kv"><tbody>{status_rows}</tbody></table>
        <p class="muted small">Optional tools with facts: {_e(active_tools)}</p>
      </div>
    </div>
    <div>
      <div class="card">
        <div class="card-h">Run a build</div>
        <div class="btnrow">
          <button class="btn primary" data-build="full" {disabled}>Rebuild (full)</button>
          <button class="btn ghost" data-build="incremental" {disabled}>Incremental</button>
          <span id="job-state" class="jobstate">{'running…' if running else 'idle'}</span>
        </div>
        <pre id="job-log" class="joblog" data-kind="build"></pre>
      </div>
    </div>
  </div>
</div>"""
    return layout("Build", "/build", body)


def _track_cell(tracks: dict) -> str:
    if not tracks:
        return "<span class='muted'>—</span>"
    return " ".join(
        f"<span class='pill score'>{_e(t)} "
        f"{('%.1f' % v) if isinstance(v, (int, float)) else '—'}</span>"
        for t, v in tracks.items()
    )


def evals_page(reports: list[dict], datasets: list[str], projects: list[dict] | None = None,
               enrich_eval: dict | None = None) -> str:
    opts = "".join(f"<option value='{_e(d)}'>{_e(d)}</option>" for d in datasets)
    proj_opts = "".join(f"<option value='{_e(p['name'])}'>{_e(p['name'])}</option>"
                        for p in (projects or []))
    ds_opts = "".join(f"<option value='{_e(d)}'>{_e(d)}</option>" for d in datasets)
    rep_rows = "".join(
        f"<tr data-report='{_e(r['name'])}'>"
        f"<td>{'★ ' if r.get('baseline') else ''}<span class='mono small'>{_e(r.get('run') or r['name'])}</span></td>"
        f"<td>{_track_cell(r.get('tracks', {}))}</td></tr>"
        for r in reports
    ) or "<tr><td colspan='2' class='muted'>No reports yet — run the suite.</td></tr>"
    body = f"""<div class="container">
  <h1>Evaluations</h1>
  <p class="sub">Run UCI's own eval suite and browse reports. The <b>supported</b> track is the
  regression gate; <b>mainframe</b> is a progress meter until the COBOL extractors land.</p>
  {_enrich_eval_teaser(enrich_eval or {})}
  <div class="card">
    <div class="card-h">Run</div>
    <div class="btnrow">
      <label class="lbl">Dataset
        <select id="eval-dataset"><option value="">all datasets</option>{opts}</select>
      </label>
      <label class="lbl chk"><input type="checkbox" id="eval-baseline"> gate vs baseline</label>
      <button class="btn primary" id="eval-run">Run evaluation</button>
      <span id="job-state" class="jobstate">idle</span>
    </div>
    <pre id="job-log" class="joblog" data-kind="eval"></pre>
  </div>
  <div class="card" style="margin-top:18px">
    <div class="card-h">Create an eval from a project</div>
    <p class="muted small">Snapshots the selected project's current extraction (symbols, resolved
    calls, queries, impact) into a golden dataset you can then run and edit below.</p>
    <div class="btnrow">
      <label class="lbl">Project <select id="eval-create-project">{proj_opts}</select></label>
      <label class="lbl">Name <input id="eval-create-name" class="pathin" style="min-width:180px" placeholder="my-repo-snapshot"></label>
      <button class="btn primary" id="eval-create">Create dataset</button>
      <span id="eval-create-msg" class="muted small"></span>
    </div>
  </div>
  <div class="card" style="margin-top:18px">
    <div class="card-h">Edit a dataset <span id="eval-edit-version" class="muted small"></span></div>
    <p class="muted small">Every save creates a new version. Use History to view or restore a
    previous version (restoring appends a new version — history is never lost).</p>
    <div class="btnrow">
      <label class="lbl">Dataset <select id="eval-edit-select"><option value="">—</option>{ds_opts}</select></label>
      <button class="btn ghost small" id="eval-edit-load">Load</button>
      <button class="btn primary small" id="eval-edit-save">Save new version</button>
      <label class="lbl">History <select id="eval-edit-history"><option value="">—</option></select></label>
      <button class="btn ghost small" id="eval-edit-restore">Restore</button>
      <span id="eval-edit-msg" class="muted small"></span>
    </div>
    <div class="viewtabs">
      <button type="button" class="viewtab active" data-view="json">JSON</button>
      <button type="button" class="viewtab" data-view="readable">Readable</button>
    </div>
    <textarea id="eval-edit-text" class="jsonedit" spellcheck="false" placeholder="Load a dataset to edit its golden JSON…"></textarea>
    <div id="eval-edit-readable" class="ds-readable" hidden></div>
  </div>
  <div class="eval-cols" style="margin-top:22px">
    <div class="card">
      <div class="card-h">Reports</div>
      <table class="tbl reports"><thead><tr><th>run</th><th>tracks</th></tr></thead>
      <tbody id="report-list">{rep_rows}</tbody></table>
    </div>
    <div class="card">
      <div class="card-h">Report detail</div>
      <div id="report-view"><span class="muted small">Select a report to view its dataset × category matrix.</span></div>
    </div>
  </div>
</div>"""
    return layout("Evals", "/evals", body)


def _enrich_eval_teaser(ev: dict) -> str:
    """Compact LLM-enrichment coverage strip on the Evals page — makes the eval discoverable
    from the tab people click when hunting for 'evals', and links to the full Enrich view."""
    if not ev:
        return ""
    su, cap, fl = ev.get("summaries", {}), ev.get("capabilities", {}), ev.get("fields", {})
    hon = ev.get("honesty", {})
    pct = lambda x: f"{round((x or 0) * 100)}%"
    honesty = ("<span class='pill score' style='color:var(--green);border-color:var(--green)'>honest</span>"
               if hon.get("ok", True) else
               "<span class='pill score' style='color:var(--red);border-color:var(--red)'>LEAK</span>")
    return f"""<div class="card" style="margin-top:18px">
    <div class="card-h">LLM enrichment eval {honesty}</div>
    <p class="muted small">Separate from the retrieval suite below \u2014 coverage of the optional LLM
      passes. Facts are labeled <span class="mono">llm:&lt;model&gt;</span> at confidence &lt; 1.0 and
      never enter the resolution ladder, so this can never inflate the retrieval scores.</p>
    <div class="btnrow">
      <span class="pill">summaries {pct(su.get('coverage'))} <span class="muted">{su.get('covered', 0)}/{su.get('eligible', 0)}</span></span>
      <span class="pill">capabilities {cap.get('count', 0)}</span>
      <span class="pill">field dictionaries {pct(fl.get('coverage'))} <span class="muted">{fl.get('with_dictionary', 0)}/{fl.get('copybooks', 0)}</span></span>
      <a class="btn ghost small" href="/enrich">Open Enrich \u2192</a>
    </div>
  </div>"""


def evals_unavailable_page() -> str:
    body = """<div class="container"><h1>Evaluations</h1>
  <p class="muted">The eval suite (<span class="mono">evals/</span>) isn't part of this workspace, so
  there's nothing to run here. Serve the UCI project itself to use this tab.</p></div>"""
    return layout("Evals", "/evals", body)


def _project_row(p: dict) -> str:
    name = _e(p["name"])
    dot = '<span class="dot-active"></span> ' if p.get("active") else ""
    status = "indexed" if p.get("indexed") else "<span class='muted'>not indexed</span>"
    activate = "" if p.get("active") else f'<button class="btn ghost small" data-activate="{name}">activate</button>'
    actions = (f'{activate}'
               f'<button class="btn ghost small" data-index="{name}">index</button>'
               f'<button class="btn ghost small danger" data-remove="{name}">remove</button>')
    return (f"<tr><td>{dot}<b>{name}</b></td>"
            f"<td class='mono small'>{_e(p['path'])}</td>"
            f"<td>{status}</td>"
            f"<td class='sc'>{p.get('entities', 0)}</td>"
            f"<td class='btnrow'>{actions}</td></tr>")


def projects_page(projects: list[dict], active: str | None) -> str:
    rows = "".join(_project_row(p) for p in projects) or \
        "<tr><td colspan='5' class='muted'>No projects yet — add one below.</td></tr>"
    body = f"""<div class="container">
  <h1>Projects</h1>
  <p class="sub">Each project is indexed into its <b>own</b> database
    (<span class="mono">&lt;path&gt;/.uci/uci.db</span>) — no cross-project bleed. Switch the active
    project from the top-right selector.</p>
  <div class="card">
    <div class="card-h">Registered projects</div>
    <table id="project-table" class="tbl"><thead><tr>
      <th>name</th><th>path</th><th>status</th><th class="sc">entities</th><th>actions</th>
    </tr></thead><tbody>{rows}</tbody></table>
  </div>
  <div class="card" style="margin-top:18px">
    <div class="card-h">Add a project</div>
    <div class="btnrow">
      <input id="proj-path" class="pathin" placeholder="/absolute/path/to/repository" autocomplete="off">
      <button class="btn primary" id="proj-add">Add &amp; index</button>
      <span id="job-state" class="jobstate">idle</span>
    </div>
    <pre id="job-log" class="joblog"></pre>
  </div>
</div>"""
    return layout("Projects", "/projects", body)


def no_projects_page() -> str:
    body = """<div class="container"><h1>No project selected</h1>
  <p class="muted">Add a repository on the <a href="/projects">Projects</a> page to start exploring.</p></div>"""
    return layout("Overview", "/", body)


def _cfg_badge(field: str, ov: set) -> str:
    return " <span class='pill score'>overridden</span>" if field in ov else ""


def _cfg_text(cfg: dict, ov: set, field: str, label: str) -> str:
    return (f"<label class='cfg-row'><span class='cfg-l'>{_e(label)}{_cfg_badge(field, ov)}</span>"
            f"<input class='cfg-in' data-cfg='{field}' value=\"{_e(cfg.get(field, ''))}\"></label>")


def _cfg_num(cfg: dict, ov: set, field: str, label: str, step: str = "any") -> str:
    return (f"<label class='cfg-row'><span class='cfg-l'>{_e(label)}{_cfg_badge(field, ov)}</span>"
            f"<input class='cfg-in' type='number' step='{step}' data-cfg='{field}' value='{_e(cfg.get(field, 0))}'></label>")


def _cfg_select(cfg: dict, ov: set, field: str, label: str, options: tuple) -> str:
    opts = "".join(
        f"<option value='{_e(o)}'{' selected' if cfg.get(field) == o else ''}>{_e(o)}</option>"
        for o in options)
    return (f"<label class='cfg-row'><span class='cfg-l'>{_e(label)}{_cfg_badge(field, ov)}</span>"
            f"<select class='cfg-in' data-cfg='{field}'>{opts}</select></label>")


def _cfg_check(cfg: dict, ov: set, field: str, label: str) -> str:
    checked = " checked" if cfg.get(field) else ""
    return (f"<label class='cfg-row cfg-check'><span class='cfg-l'>{_e(label)}{_cfg_badge(field, ov)}</span>"
            f"<input type='checkbox' data-cfg='{field}'{checked}></label>")


def _cfg_csv(cfg: dict, ov: set, field: str, label: str) -> str:
    val = ", ".join(cfg.get(field, []) or [])
    return (f"<label class='cfg-row'><span class='cfg-l'>{_e(label)}{_cfg_badge(field, ov)}</span>"
            f"<input class='cfg-in' data-cfg='{field}' value=\"{_e(val)}\"></label>")


def config_page(cfg: dict, overrides: dict, reindex_fields=()) -> str:
    ov = set(overrides or {})
    backends = (
        _cfg_select(cfg, ov, "profile", "Profile", ("local-lite", "local-pro", "cloud")) +
        _cfg_select(cfg, ov, "graph_backend", "Graph backend", ("sqlite", "memgraph", "neo4j")) +
        _cfg_select(cfg, ov, "vector_backend", "Vector backend", ("sqlite", "qdrant")) +
        _cfg_select(cfg, ov, "metadata_backend", "Metadata backend", ("sqlite", "postgres"))
    )
    embeddings = (
        _cfg_select(cfg, ov, "embedding_provider", "Provider", ("local", "noop", "ollama", "openai")) +
        _cfg_text(cfg, ov, "embedding_model", "Model") +
        _cfg_num(cfg, ov, "embedding_dim", "Dimensions", "1")
    )
    ingest = (
        _cfg_check(cfg, ov, "use_gitignore", "Respect .gitignore") +
        _cfg_check(cfg, ov, "index_all_text", "Index all text files") +
        _cfg_num(cfg, ov, "max_file_bytes", "Max file bytes", "1000") +
        _cfg_num(cfg, ov, "max_chunk_lines", "Max chunk lines", "1") +
        _cfg_num(cfg, ov, "window_lines", "Window lines", "1") +
        _cfg_num(cfg, ov, "window_overlap", "Window overlap", "1")
    )
    weights = "".join(_cfg_num(cfg, ov, f"weight_{w}", w.capitalize())
                      for w in ("symbol", "keyword", "semantic", "graph", "proximity", "churn"))
    weights += _cfg_num(cfg, ov, "rrf_k", "RRF k", "1")
    llm = (
        _cfg_select(cfg, ov, "llm_protocol", "Protocol", ("ollama", "openai", "anthropic", "freellm")) +
        _cfg_text(cfg, ov, "llm_url", "Base URL (blank = default)") +
        _cfg_text(cfg, ov, "llm_model", "Model (blank = default)") +
        _cfg_num(cfg, ov, "llm_timeout", "Timeout (s)", "1") +
        _cfg_num(cfg, ov, "llm_max_tokens", "Max tokens", "1")
    )
    body = f"""<div class="container">
  <h1>Configuration</h1>
  <p class="sub">Settings for the active project, persisted to <span class="mono">.uci/overrides.json</span>.
    Weights apply immediately; embedding &amp; ingest changes need a <a href="/build">re-index</a>.</p>
  <div class="split">
    <div>
      <div class="card"><div class="card-h">Profile &amp; backends</div>{backends}</div>
      <div class="card" style="margin-top:16px"><div class="card-h">Embeddings <span class="muted small">re-index</span></div>{embeddings}</div>
      <div class="card" style="margin-top:16px"><div class="card-h">Ingest <span class="muted small">re-index</span></div>{ingest}</div>
    </div>
    <div>
      <div class="card"><div class="card-h">Retrieval weights</div>{weights}</div>
      <div class="card" style="margin-top:16px"><div class="card-h">Gaps</div>{_cfg_csv(cfg, ov, "gap_external_prefixes", "External prefixes (comma-separated)")}</div>
      <div class="card" style="margin-top:16px"><div class="card-h">LLM enrichment</div>{llm}
        <p class="muted small">API keys stay in the environment (<span class="mono">UCI_*</span>), never stored here. Run passes in the <a href="/enrich">Enrich</a> tab.</p></div>
      <div class="card" style="margin-top:16px"><div class="card-h">Paths (read-only)</div>
        <table class="kv"><tbody>
          <tr><td>repo</td><td class="mono small">{_e(cfg.get('repo_path'))}</td></tr>
          <tr><td>store</td><td class="mono small">{_e(cfg.get('store_dir'))}</td></tr>
        </tbody></table>
      </div>
    </div>
  </div>
  <div class="btnrow" style="margin-top:18px">
    <button class="btn primary" id="cfg-save">Save configuration</button>
    <button class="btn ghost" id="cfg-reset">Reset to defaults</button>
    <span id="cfg-msg" class="muted small"></span>
  </div>
</div>"""
    return layout("Config", "/config", body)


def enrich_page(status: dict, passes, ev: dict | None = None) -> str:
    available = status.get("available")
    badge = ("<span class='pill score' style='color:var(--green);border-color:var(--green)'>reachable</span>"
             if available else
             "<span class='pill score' style='color:var(--amber);border-color:var(--amber)'>not reachable</span>")
    status_rows = "".join(
        f"<tr><td>{_e(k)}</td><td class='mono small'>{_e(v)}</td></tr>" for k, v in (
            ("protocol", status.get("protocol") or "—"),
            ("model", status.get("model") or "—"),
            ("endpoint", status.get("base_url") or "—"),
            ("existing summaries", status.get("summaries", 0)),
        ))
    checks = "".join(
        f"<label class='lbl chk'><input type='checkbox' class='enrich-pass' value='{_e(p)}' checked> {_e(p)}</label>"
        for p in passes)
    warn = "" if available else (
        "<p class='muted small'>No reachable LLM provider. Configure one in "
        "<a href='/config'>Config → LLM enrichment</a> (protocol / model / URL) or set "
        "<span class='mono'>UCI_LLM_*</span> — see <span class='mono'>docs/llm-enrichment.md</span>."
        + (f" <span class='muted'>({_e(status.get('error'))})</span>" if status.get("error") else "") + "</p>")
    body = f"""<div class="container">
  <h1>LLM enrichment {badge}</h1>
  <p class="sub">Optional passes add <b>purpose summaries</b>, <b>business capabilities</b>,
    <b>dynamic-call candidates</b>, <b>field dictionaries</b>, and a <b>system-architecture overview</b>. Every fact is labeled
    <span class="mono">extractor="llm:&lt;model&gt;"</span> with confidence &lt; 1.0 (candidates use
    <span class="mono">resolution="llm-suggested"</span>) — the resolution ladder and completeness stay honest.</p>
  <div class="split">
    <div>
      <div class="card"><div class="card-h">Run enrichment</div>
        {warn}
        <div class="btnrow">{checks}</div>
        <div class="btnrow">
          <label class="lbl">Limit <input id="enrich-limit" class="cfg-in" type="number" step="1" value="200" style="min-width:90px"></label>
          <label class="lbl chk"><input type="checkbox" id="enrich-force"> force (ignore cache)</label>
          <button class="btn primary" id="enrich-run">Run enrichment</button>
          <span id="job-state" class="jobstate">idle</span>
        </div>
        <pre id="job-log" class="joblog" data-kind="enrich"></pre>
      </div>
    </div>
    <div>
      <div class="card"><div class="card-h">Provider</div>
        <table class="kv"><tbody>{status_rows}</tbody></table>
      </div>
    </div>
  </div>
  {_enrich_results(ev or {})}
</div>"""
    return layout("Enrich", "/enrich", body)


def _enrich_results(ev: dict) -> str:
    if not ev:
        return ""
    su, cap, cand, fl = ev.get("summaries", {}), ev.get("capabilities", {}), ev.get("candidates", {}), ev.get("fields", {})
    arch = ev.get("architecture", {})
    hon = ev.get("honesty", {})
    pct = lambda x: f"{round((x or 0) * 100)}%"
    honesty = ("<span class='pill score' style='color:var(--green);border-color:var(--green)'>honest</span>"
               if hon.get("ok", True) else
               "<span class='pill score' style='color:var(--red);border-color:var(--red)'>LEAK</span>")
    rows = (
        f"<tr><td>summaries</td><td class='sc'>{pct(su.get('coverage'))}</td>"
        f"<td class='muted small'>{su.get('covered', 0)}/{su.get('eligible', 0)} eligible · avg {su.get('avg_chars', 0)} chars</td></tr>"
        f"<tr><td>capabilities</td><td class='sc'>{pct(cap.get('coverage'))}</td>"
        f"<td class='muted small'>{cap.get('count', 0)} capabilities · {cap.get('mapped', 0)}/{cap.get('programs', 0)} programs mapped</td></tr>"
        f"<tr><td>dynamic candidates</td><td class='sc'>{pct(cand.get('precision'))}</td>"
        f"<td class='muted small'>{cand.get('valid_targets', 0)}/{cand.get('edges', 0)} llm-suggested edges hit indexed targets</td></tr>"
        f"<tr><td>field dictionaries</td><td class='sc'>{pct(fl.get('coverage'))}</td>"
        f"<td class='muted small'>{fl.get('with_dictionary', 0)}/{fl.get('copybooks', 0)} copybooks</td></tr>"
        f"<tr><td>architecture overview</td><td class='sc'>{'yes' if arch.get('present') else 'no'}</td>"
        f"<td class='muted small'>{('generated by ' + _e(arch.get('model', ''))) if arch.get('present') else 'not generated yet'}"
        f" · {arch.get('key_points', 0)} key points</td></tr>"
    )
    return f"""<div class="card" style="margin-top:18px">
    <div class="card-h">Results — enrichment eval {honesty}</div>
    <table><thead><tr><th>pass</th><th class="sc">coverage / precision</th><th>detail</th></tr></thead>
    <tbody>{rows}</tbody></table>
    <p class="muted small">Honesty invariant: {hon.get('llm_suggested_edges', 0)} llm-suggested edges,
      {hon.get('leaked_into_ladder', 0)} leaked into the resolution ladder (must be 0). LLM facts stay
      in the candidate stratum at confidence &lt; 1.0, so multi-hop traversal and completeness never trust them.</p>
  </div>"""


def _db_grid(columns: list, rows: list) -> str:
    head = "".join(f"<th>{_e(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e('' if v is None else v)}</td>" for v in row) + "</tr>"
        for row in rows
    ) or f"<tr><td colspan='{max(1, len(columns))}' class='muted'>No rows.</td></tr>"
    return (f"<div class='tablewrap'><table class='dbtable'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def _db_browse(data: dict | None) -> str:
    if not data or not data.get("ok"):
        msg = (data or {}).get("error", {}).get("message", "table not available")
        return f"<div class='card'><p class='muted'>{_e(msg)}</p></div>"
    limit, offset, total = data["limit"], data["offset"], data["total"]
    page = offset // limit if limit else 0
    table = data["table"]
    prev = (f"<a class='btn ghost small' href='/db?table={quote(table)}&page={page - 1}'>&larr; prev</a>"
            if page > 0 else "")
    nxt = (f"<a class='btn ghost small' href='/db?table={quote(table)}&page={page + 1}'>next &rarr;</a>"
           if offset + limit < total else "")
    start = offset + 1 if total else 0
    end = min(offset + limit, total)
    return (f"<div class='card'><div class='card-h'>{_e(table)} "
            f"<span class='muted small'>&middot; {start}&ndash;{end} of {total}</span></div>"
            f"{_db_grid(data['columns'], data['rows'])}"
            f"<div class='btnrow' style='margin-top:10px'>{prev}{nxt}</div></div>")


def _db_result(result: dict | None) -> str:
    if not result or not result.get("ok"):
        msg = (result or {}).get("error", {}).get("message", "query failed")
        return (f"<div class='card'><div class='card-h'>Query error</div>"
                f"<p class='muted mono small'>{_e(msg)}</p></div>")
    cap = " <span class='muted small'>(capped)</span>" if result.get("capped") else ""
    return (f"<div class='card'><div class='card-h'>Result "
            f"<span class='muted small'>&middot; {result['row_count']} rows{cap}</span></div>"
            f"{_db_grid(result['columns'], result['rows'])}</div>")


def db_page(tables: list, table: str, data: dict | None, sql: str, result: dict | None) -> str:
    chips = "".join(
        f'<a href="/db?table={quote(t["table"])}" '
        f'class="dbchip{" active" if (t["table"] == table and not sql) else ""}">'
        f'{_e(t["table"])} <span class="muted small">{t["rows"]}</span></a>'
        for t in tables)
    sqlbox = (
        '<form method="get" action="/db" class="card" style="margin-bottom:16px">'
        '<div class="card-h">Read-only SQL</div>'
        '<textarea class="sqlbox" name="sql" rows="3" spellcheck="false" '
        'placeholder="SELECT kind, COUNT(*) FROM entities GROUP BY kind ORDER BY 2 DESC">'
        f'{_e(sql)}</textarea>'
        '<div class="btnrow" style="margin-top:8px">'
        '<button class="btn primary" type="submit">Run query</button>'
        '<a class="btn ghost small" href="/db">clear</a>'
        '<span class="muted small">SELECT / WITH only &middot; runs on a read-only connection '
        '&middot; rows capped</span></div></form>')
    panel = _db_result(result) if sql else _db_browse(data)
    body = (
        '<div class="container wide"><h1>Database</h1>'
        '<p class="sub">Read-only view of the index store '
        '(<span class="mono">.uci/uci.db</span>), scoped to this repository.</p>'
        f'<div class="pillrow" style="margin-bottom:16px">{chips}</div>'
        f'{sqlbox}{panel}</div>')
    return layout("Database", "/db", body)


def docs_page(data: dict) -> str:
    cov = data.get("coverage", {})
    documents = data.get("documents", [])
    undoc = data.get("undocumented", [])
    if not documents:
        body = ('<div class="container"><h1>Documentation</h1>'
                '<p class="sub">No documentation indexed yet. Add README/spec files, or check '
                '<span class="mono">UCI_INDEX_DOCS</span>.</p></div>')
        return layout("Docs", "/docs", body)
    stat = (f'<div class="card"><div class="card-h">Coverage</div>'
            f'<p><b style="font-size:22px">{cov.get("pct", 0)}%</b> '
            f'<span class="muted">of key artifacts documented '
            f'({cov.get("described", 0)}/{cov.get("total", 0)})</span></p></div>')
    doc_rows = "".join(
        f'<tr><td><a href="/docs?path={quote(d["path"])}">{_e(d["path"])}</a></td>'
        f'<td>{d["sections"]}</td><td>{d["links"]}</td></tr>' for d in documents)
    doc_table = (f'<div class="card"><div class="card-h">Documents</div>'
                 f'<table><thead><tr><th>path</th><th>sections</th><th>links</th></tr></thead>'
                 f'<tbody>{doc_rows}</tbody></table></div>')
    undoc_block = ""
    if undoc:
        rows = "".join(
            f'<tr><td>{_kind_pill(u["kind"])} <a href="/search?q={quote(u["name"])}">{_e(u["name"])}</a></td>'
            f'<td class="mono muted small">{_e(u["path"])}</td></tr>' for u in undoc[:50])
        undoc_block = (f'<div class="card"><div class="card-h">Undocumented '
                       f'<span class="muted small">· {len(undoc)}</span></div>'
                       f'<table><thead><tr><th>artifact</th><th>path</th></tr></thead>'
                       f'<tbody>{rows}</tbody></table></div>')
    body = (f'<div class="container"><h1>Documentation</h1>'
            f'<p class="sub">Docs ingested into the graph, linked to the code they describe.</p>'
            f'{stat}{doc_table}{undoc_block}</div>')
    return layout("Docs", "/docs", body)


def doc_detail_page(data: dict) -> str:
    path = data.get("path", "")
    sections = data.get("sections", [])
    if not sections:
        body = (f'<div class="container"><h1>{_e(path)}</h1>'
                f'<p class="sub"><a href="/docs">&larr; all docs</a></p>'
                f'<p class="muted">No sections.</p></div>')
        return layout("Doc", "/docs", body)
    blocks = []
    for sec in sections:
        links = "".join(
            f'<a class="pill" href="/search?q={quote(link["name"])}">{_e(link["name"])} '
            f'<span class="muted small">{_e(link["resolution"])}</span></a> '
            for link in sec.get("links", []))
        link_row = f'<div class="pillrow" style="margin:6px 0">{links}</div>' if links else ""
        text = _e(sec.get("text", ""))[:1500]
        blocks.append(
            f'<div class="card"><div class="card-h">{_e(sec["heading"])} '
            f'<span class="muted small">· lines {sec["start_line"]}–{sec["end_line"]}</span></div>'
            f'{link_row}<pre class="dsjson" style="max-height:220px">{text}</pre></div>')
    body = (f'<div class="container"><h1>{_e(path)}</h1>'
            f'<p class="sub"><a href="/docs">&larr; all docs</a></p>{"".join(blocks)}</div>')
    return layout("Doc", "/docs", body)


def _metrics_reindex_body(msg: str) -> str:
    return f"""<div class="container">
  <h1>Code metrics</h1>
  <p class="sub">{_e(msg)}</p>
  <div class="card">
    <div class="card-h">Collect metrics</div>
    <p class="muted small">Metrics are gathered during indexing. Re-index this project to collect them.</p>
    <div class="btnrow">
      <button class="btn primary" data-build="full">Re-index now</button>
      <span id="job-state" class="jobstate">idle</span>
    </div>
    <pre id="job-log" class="joblog" data-kind="build"></pre>
  </div>
</div>"""


def metrics_page(data: dict) -> str:
    if not data.get("ok"):
        msg = data.get("error", {}).get("message", "Metrics not collected yet.")
        return layout("Metrics", "/metrics", _metrics_reindex_body(msg))

    m = data["metrics"]
    lines = m.get("lines", {})
    cards = "".join(
        f'<div class="card stat"><div class="n">{v}</div><div class="l">{_e(label)}</div></div>'
        for label, v in (
            ("files", m.get("files", 0)),
            ("lines", lines.get("total", 0)),
            ("code", lines.get("code", 0)),
            ("comment", lines.get("comment", 0)),
            ("blank", lines.get("blank", 0)),
            ("comment ratio", f"{round(lines.get('comment_ratio', 0) * 100)}%"),
        )
    )
    lang_rows = "".join(
        f"<tr><td>{_e(lang)}</td><td class='sc'>{b.get('files', 0)}</td><td class='sc'>{b.get('code', 0)}</td>"
        f"<td class='sc'>{b.get('comment', 0)}</td><td class='sc'>{b.get('blank', 0)}</td>"
        f"<td class='sc'>{b.get('total', 0)}</td></tr>"
        for lang, b in m.get("by_language", {}).items()
    ) or "<tr><td colspan='6' class='muted'>—</td></tr>"

    resolved_levels = {"syntactic", "import-traced", "inherited", "inferred"}
    dist = m.get("call_resolution_distribution", {})
    total_calls = sum(dist.values()) or 1
    resolved_n = sum(v for k, v in dist.items() if k in resolved_levels)
    res_rows = "".join(
        f"<tr><td>{_e(k)}</td><td class='sc'>{v}</td><td class='sc'>{round(v / total_calls * 100)}%</td></tr>"
        for k, v in dist.items()
    ) or "<tr><td colspan='3' class='muted'>no call edges</td></tr>"

    ep = m.get("entry_points", {})
    cd = m.get("cross_dependencies", {})
    kind_rows = "".join(f"<tr><td>{_e(k)}</td><td class='sc'>{v}</td></tr>"
                        for k, v in m.get("entities_by_kind", {}).items())
    rel_rows = "".join(f"<tr><td>{_e(k)}</td><td class='sc'>{v}</td></tr>"
                       for k, v in m.get("relationships_by_type", {}).items())
    hub_rows = "".join(
        f"<tr><td class='mono small'>{_e(h['name'])}</td><td>{_kind_pill(h['kind'])}</td>"
        f"<td class='sc'>{h['callers']}</td></tr>"
        for h in m.get("top_fan_in", [])
    ) or "<tr><td colspan='3' class='muted'>—</td></tr>"

    body = f"""<div class="container">
  <h1>Code metrics</h1>
  <p class="sub">Collected at index time. “Resolved” = share of call edges the resolution ladder
    attributed exactly (syntactic / import-traced / inherited / inferred).</p>
  <div class="grid cards">{cards}</div>
  <div class="split" style="margin-top:22px">
    <div>
      <div class="card">
        <div class="card-h">Call resolution — {round(resolved_n / total_calls * 100)}% resolved</div>
        <table><thead><tr><th>level</th><th class="sc">count</th><th class="sc">share</th></tr></thead>
        <tbody>{res_rows}</tbody></table>
        <p class="muted small">unresolved sites {m.get('unresolved_call_sites', 0)} ·
          dynamic {m.get('dynamic_call_sites', 0)} · external deps {m.get('external_dependencies', 0)} ·
          missing {m.get('missing_artifacts', 0)}</p>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-h">By language</div>
        <table><thead><tr><th>language</th><th class="sc">files</th><th class="sc">code</th>
        <th class="sc">comment</th><th class="sc">blank</th><th class="sc">total</th></tr></thead>
        <tbody>{lang_rows}</tbody></table>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-h">Top fan-in</div>
        <table><thead><tr><th>symbol</th><th>kind</th><th class="sc">callers</th></tr></thead>
        <tbody>{hub_rows}</tbody></table>
      </div>
    </div>
    <div>
      <div class="card">
        <div class="card-h">Entry points — {ep.get('total', 0)}</div>
        <table class="kv"><tbody>
          <tr><td>python main guards</td><td class='sc'>{ep.get('python_main_guards', 0)}</td></tr>
          <tr><td>uncalled programs</td><td class='sc'>{ep.get('uncalled_programs', 0)}</td></tr>
          <tr><td>JCL jobs</td><td class='sc'>{ep.get('jcl_jobs', 0)}</td></tr>
          <tr><td>CICS transactions</td><td class='sc'>{ep.get('cics_transactions', 0)}</td></tr>
        </tbody></table>
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-h">Coupling</div>
        <table class="kv"><tbody>
          <tr><td>cross-file edges</td><td class='sc'>{cd.get('cross_file_edges', 0)}</td></tr>
          <tr><td>cross-directory edges</td><td class='sc'>{cd.get('cross_directory_edges', 0)}</td></tr>
        </tbody></table>
      </div>
    </div>
  </div>
  <div class="split" style="margin-top:22px">
    <div><div class="card"><div class="card-h">Entities by kind</div>
      <table><thead><tr><th>kind</th><th class="sc">count</th></tr></thead><tbody>{kind_rows}</tbody></table></div></div>
    <div><div class="card"><div class="card-h">Relationships by type</div>
      <table><thead><tr><th>type</th><th class="sc">count</th></tr></thead><tbody>{rel_rows}</tbody></table></div></div>
  </div>
</div>"""
    return layout("Metrics", "/metrics", body)


def unindexed_page(name: str) -> str:
    body = f"""<div class="container">
  <h1>{_e(name or 'This project')} isn't indexed yet</h1>
  <p class="sub">This project is registered but has no index in <span class="mono">.uci/</span>
    (for example, an eval run with <span class="mono">--clean</span> may have removed it). Build it
    to explore its graph, symbols, and impact.</p>
  <div class="card">
    <div class="card-h">Index this project</div>
    <div class="btnrow">
      <button class="btn primary" data-build="full">Index now</button>
      <span id="job-state" class="jobstate">idle</span>
    </div>
    <pre id="job-log" class="joblog" data-kind="build"></pre>
  </div>
</div>"""
    return layout("Index", "/build", body)


def _kind_pill(kind: str) -> str:
    return f'<span class="pill k-{_e(kind)}">{_e(kind)}</span>'


def _loc_link(path: str, start: int, end: int, label: str | None = None) -> str:
    if not path:
        return _e(label or "")
    loc = f"{path}:{start}" if start else path
    return f'<span class="mono loc">{_e(loc)}</span>'


def _symbol_link(qname: str, entity_id: str, label: str | None = None) -> str:
    return f'<a href="/symbol?id={quote(entity_id)}">{_e(label or qname)}</a>'


# --------------------------------------------------------------------------- pages
def overview_page(data: dict) -> str:
    t = data.get("totals", {})
    cards = "".join(
        f'<div class="card stat"><div class="n">{t.get(key,0)}</div><div class="l">{_e(key)}</div></div>'
        for key in ("files", "modules", "functions", "classes", "tests", "config_keys")
    )
    langs = ", ".join(f"{_e(k)} ({v})" for k, v in data.get("languages", {}).items()) or "—"
    deps = ", ".join(_e(d) for d in data.get("external_dependencies", [])) or "—"

    key_rows = "".join(
        f"<tr><td>{_symbol_link(s['qualified_name'], '', s['name'])}</td>"
        f"<td>{_kind_pill(s['kind'])}</td>"
        f"<td class='mono'>{_e(s['path'])}</td>"
        f"<td>{s['callers']}</td></tr>"
        for s in data.get("key_symbols", [])[:12]
    ) or "<tr><td colspan=4 class='muted'>No call relationships yet.</td></tr>"

    mod_rows = "".join(
        f"<tr><td><a href='/module?q={quote(m['qualified_name'])}'>{_e(m['qualified_name'])}</a></td>"
        f"<td class='mono'>{_e(m['path'])}</td><td>{m['symbols']}</td></tr>"
        for m in data.get("modules", [])[:15]
    )
    ep = "".join(
        f"<li>{_symbol_link(e['qualified_name'], '', e['name'])} "
        f"<span class='mono muted'>{_e(e['path'])}</span></li>"
        for e in data.get("entry_points", [])[:8]
    ) or "<li class='muted'>None detected.</li>"

    body = f"""<div class="container">
  <h1>{_e(data.get('name') or 'Repository')} overview</h1>
  <p class="sub">Graph-derived summary. <span class="mono">{_e(data.get('repo_id'))}</span></p>
  <div class="grid cards">{cards}</div>
  <div class="split" style="margin-top:22px">
    <div>
      <h2>Most-referenced symbols</h2>
      <table><thead><tr><th>Symbol</th><th>Kind</th><th>Path</th><th>Callers</th></tr></thead>
      <tbody>{key_rows}</tbody></table>
      <h2>Modules</h2>
      <table><thead><tr><th>Module</th><th>Path</th><th>Symbols</th></tr></thead>
      <tbody>{mod_rows}</tbody></table>
    </div>
    <div>
      <div class="card"><h2 style="margin-top:0">Languages</h2><p>{langs}</p></div>
      <div class="card"><h2 style="margin-top:0">External deps</h2><p class="mono">{deps}</p></div>
      <div class="card"><h2 style="margin-top:0">Entry points</h2><ul class="clean">{ep}</ul></div>
    </div>
  </div>
</div>"""
    return layout("Overview", "/", body)


def search_page(query: str, results: list[dict]) -> str:
    hits = ""
    for r in results:
        signals = "".join(f'<span class="s">{_e(s)}</span>' for s in r.get("signals", []))
        hits += f"""<div class="hit">
          <div>{_symbol_link(r['qualified_name'], r['entity_id'], r['name'])} {_kind_pill(r['kind'])}
          <span class="signals">{signals}</span></div>
          <div class="loc">{_loc_link(r['path'], r['start_line'], r['end_line'])} · score {r['score']:.3f} · conf {r['confidence']}</div>
          <div class="reason">{_e(r['reason'])}</div>
        </div>"""
    if not results and query:
        hits = "<p class='muted'>No results.</p>"
    body = f"""<div class="container">
  <h1>Search</h1>
  <p class="sub">Graph-first hybrid search — symbol, keyword, semantic, and graph signals fused.</p>
  <form class="searchbox" method="get" action="/search">
    <input name="q" value="{_e(query)}" placeholder="e.g. where is pricing validation implemented?" autofocus>
    <button type="submit">Search</button>
  </form>
  {hits}
</div>"""
    return layout("Search", "/search", body)


def graph_page(root_id: str, root_label: str, view: str = "repository", view_options=()) -> str:
    opts = "".join(
        f'<option value="{_e(k)}"{" selected" if k == view else ""}>{_e(label)}</option>'
        for k, label in view_options
    )
    view_select = f'<label class="lbl">angle <select id="graph-view">{opts}</select></label>' if opts else ""
    seed = f'<b>{_e(root_label)}</b>' if root_label else "the selected angle"
    body = f"""<div class="container wide">
  <h1>Graph explorer</h1>
  <p class="sub">Scroll or pinch to zoom (toward the cursor), drag to pan, click a node to open it,
    double-click to expand its neighborhood. Rooted at {seed}.
    <span id="node-info" class="mono muted"></span></p>
  <div id="graph-wrap">
    <canvas id="graph" data-root="{_e(root_id)}" data-view="{_e(view)}"></canvas>
    <div class="graph-controls">
      {view_select}
      <button type="button" data-graph="in" title="zoom in">+</button>
      <button type="button" data-graph="out" title="zoom out">−</button>
      <button type="button" data-graph="fit" title="fit to view">⤢</button>
    </div>
    <div class="legend">
      <div class="row"><span class="dot" style="background:#4c8dff"></span>function/method</div>
      <div class="row"><span class="dot" style="background:#7c5cff"></span>class/interface</div>
      <div class="row"><span class="dot" style="background:#3fb950"></span>test</div>
      <div class="row"><span class="dot" style="background:#6ea8fe"></span>module</div>
      <div class="row"><span class="dot" style="background:#d29922"></span>config/commit</div>
    </div>
    <div id="graph-tile" class="graph-tile" hidden></div>
  </div>
</div>"""
    return layout("Graph", "/graph", body)


def impact_page(query: str, data: dict) -> str:
    if not data.get("ok"):
        inner = f"<p class='muted'>{_e(data.get('error', {}).get('message', 'Not found'))}</p>" if query else ""
        body = f"""<div class="container"><h1>Impact analysis</h1>
        <p class="sub">What breaks if I change X? Traverses the graph — callers, callees, tests, config, churn.</p>
        <form class="searchbox" method="get" action="/impact">
          <input name="q" value="{_e(query)}" placeholder="e.g. PricingCalculator.calculate" autofocus>
          <button type="submit">Analyze</button></form>{inner}</div>"""
        return layout("Impact", "/impact", body)

    target = data["target"]
    risk = data.get("risk", {})
    churn = data.get("churn", {})
    comp = data.get("completeness", {})
    idx = data.get("index", {})
    callers = data.get("callers", {})
    callees = data.get("callees", {})

    def hit_list(items, empty):
        if not items:
            return f"<p class='muted'>{empty}</p>"
        rows = "".join(
            f"<li>{_symbol_link(h['qualified_name'], h['entity_id'], h['name'])} {_kind_pill(h['kind'])} "
            f"<span class='mono muted'>{_e(h['path'])}:{h['start_line']}</span> — "
            f"<span class='muted'>{_e(h['reason'])}</span>"
            f"{' <span class=pill>' + _e(h['resolution']) + '</span>' if h.get('resolution') else ''}</li>"
            for h in items
        )
        return f"<ul class='clean'>{rows}</ul>"

    factors = ", ".join(_e(f) for f in risk.get("factors", []))
    unresolved = callers.get("unresolved", {})
    unresolved_html = (
        f"<div class='flash'>⚠ {_e(unresolved.get('note'))}</div>" if unresolved.get("count") else ""
    )
    candidate_callers = (
        f"<h2>Callers — candidates ({len(callers.get('candidates', []))})</h2>"
        f"{hit_list(callers.get('candidates', []), 'None')}" if callers.get("candidates") else ""
    )
    candidate_callees = (
        f"<h2>Callees — candidates ({len(callees.get('candidates', []))})</h2>"
        f"{hit_list(callees.get('candidates', []), 'None')}" if callees.get("candidates") else ""
    )
    comp_reasons = ("; ".join(_e(r) for r in comp.get("reasons", []))) or "no gaps detected"
    stale = ""
    if idx:
        behind = idx.get("commits_behind", 0)
        stale = (f" · index gen {idx.get('generation', 0)}"
                 + (f", <b>{behind} commit(s) behind HEAD</b>" if behind else ", up to date"))
    body = f"""<div class="container">
  <h1>Impact: {_e(target['name'])}</h1>
  <p class="sub">{_kind_pill(target['kind'])} <span class="mono">{_e(target['qualified_name'])}</span>
    · <span class="mono">{_e(target['path'])}:{target['start_line']}</span></p>
  <div class="flash">Risk: <span class="pill risk-{_e(risk.get('level','low'))}">{_e(risk.get('level','low'))}</span>
    score {risk.get('score',0)} — {factors}
    &nbsp;·&nbsp; churn: {churn.get('commits',0)} commit(s)</div>
  <div class="flash">Completeness: <b>{_e(comp.get('level','?'))}</b> — {comp_reasons}{stale}</div>
  <div class="split">
    <div>
      <h2>Callers — resolved ({len(callers.get('resolved', []))}) — direct blast radius</h2>{hit_list(callers.get('resolved', []), 'No resolved callers.')}
      {candidate_callers}
      {unresolved_html}
      <h2>Callees — resolved ({len(callees.get('resolved', []))})</h2>{hit_list(callees.get('resolved', []), 'No callees found.')}
      {candidate_callees}
    </div>
    <div>
      <h2>Tests ({len(data['tests'])})</h2>{hit_list(data['tests'], 'No covering tests found.')}
      <h2>Config ({len(data['config'])})</h2>{hit_list(data['config'], 'None found.')}
      <h2>Data ({len(data['data'])})</h2>{hit_list(data['data'], 'None found.')}
    </div>
  </div>
</div>"""
    return layout("Impact", "/impact", body)


def module_page(data: dict) -> str:
    if not data.get("ok"):
        return layout("Module", "/", f"<div class='container'><p class='muted'>{_e(data.get('error',{}).get('message'))}</p></div>")
    syms = "".join(
        f"<tr><td>{_e(s['name'])}</td><td>{_kind_pill(s['kind'])}</td><td>{s['start_line']}</td>"
        f"<td class='muted'>{_e(s['docstring'])}</td></tr>"
        for s in data.get("symbols", [])
    ) or "<tr><td colspan=4 class='muted'>No symbols.</td></tr>"
    imports = ", ".join(_e(i["qualified_name"]) for i in data.get("imports", [])) or "—"
    importers = ", ".join(_e(i["qualified_name"]) for i in data.get("imported_by", [])) or "—"
    body = f"""<div class="container">
  <h1>{_e(data['module'])}</h1>
  <p class="sub">{_e(data.get('purpose'))}</p>
  <div class="card"><b>Layer:</b> {_e(data.get('layer'))} &nbsp; · &nbsp;
    <b>Path:</b> <span class="mono">{_e(data['path'])}</span> &nbsp; · &nbsp;
    <a href="/graph?id={quote(data.get('root_id',''))}">view in graph</a></div>
  <h2>Imports</h2><p class="mono">{imports}</p>
  <h2>Imported by</h2><p class="mono">{importers}</p>
  <h2>Symbols ({data.get('symbol_count',0)})</h2>
  <table><thead><tr><th>Name</th><th>Kind</th><th>Line</th><th>Doc</th></tr></thead><tbody>{syms}</tbody></table>
</div>"""
    return layout("Module", "/", body)


def flows_page(data: dict) -> str:
    """Business-capability browser: each capability with its implementing programs, triggers, and
    data. Capability-only — when enrichment hasn't produced any, show a CTA to the Enrich tab
    rather than an empty view (docs/llm-enrichment.md)."""
    caps = data.get("capabilities", [])
    if not caps:
        inner = """<div class="card">
      <div class="card-h">No business capabilities yet</div>
      <p class="muted">Flows groups programs by the <b>business capability</b> they implement. Those
        are produced by the optional LLM enrichment pass — none exist for this project yet.</p>
      <div class="btnrow"><a class="btn primary" href="/enrich">Run the capabilities pass →</a></div>
      <p class="muted small">See <span class="mono">docs/llm-enrichment.md</span>. Capabilities are
        labeled <span class="mono">llm:&lt;model&gt;</span> at confidence &lt; 1.0 and never enter the
        resolution ladder.</p>
    </div>"""
    else:
        inner = "".join(_flow_card(c) for c in caps)
    plural = "y" if len(caps) == 1 else "ies"
    body = f"""<div class="container">
  <h1>Flows <span class="tag">· business capabilities</span></h1>
  <p class="sub">Each capability with the programs that implement it, how it's triggered
    (transaction codes / JCL jobs) and the data it touches. {len(caps)} capabilit{plural}.</p>
  {inner}
</div>"""
    return layout("Flows", "/flows", body)


def _flow_card(cap: dict) -> str:
    progs = cap.get("programs", [])
    programs = "".join(
        f"<li>{_symbol_link(p['qualified_name'], p['entity_id'], p['name'])} {_kind_pill(p['kind'])}"
        + (f"<br><span class='muted small'>{_e(p['summary'])}</span>" if p.get("summary") else "")
        + "</li>"
        for p in progs
    ) or "<li class='muted'>No implementing programs.</li>"
    triggers = " ".join(
        f"{_kind_pill(t['kind'])} <span class='mono small'>{_e(t['name'])}</span>"
        for t in cap.get("triggers", [])
    )
    tables = " ".join(
        f"<span class='pill'>{_e(d['name'])} <span class='muted small'>{_e(d['access'])}</span></span>"
        for d in cap.get("data", [])
    )
    desc = f"<p class='muted'>{_e(cap['description'])}</p>" if cap.get("description") else ""
    trig_row = f"<p class='small'><b>Triggered by</b> · {triggers}</p>" if triggers else ""
    data_row = f"<p class='small'><b>Data</b> · {tables}</p>" if tables else ""
    return f"""<div class="card" style="margin-bottom:14px">
    <div class="card-h">{_e(cap['name'])} <span class="tag">· {len(progs)} programs</span></div>
    {desc}
    <ul class="clean">{programs}</ul>
    {trig_row}{data_row}
  </div>"""


def understand_page(data: dict) -> str:
    """Tutorial-style walkthrough of how this codebase is organized and how it runs — numbered
    chapters, a traced worked example, and an honest coverage section, all composed from the same
    graph the tools use (docs/dashboard.md)."""
    enriched = bool(data.get("enriched"))
    summary = data.get("summary", {})
    name = summary.get("name") or "this codebase"
    chapters = [
        ("what", "Orientation", "Start here — the 30-second picture of what you're looking at.",
         _u_what(summary)),
        ("organized", "The map", "Every codebase has a shape. These are the neighborhoods you'll navigate.",
         _u_organized(data.get("organization", {}))),
        ("runs", "Watch it run",
         "Enough structure — let's follow one real entry point all the way through, then see every other way in.",
         _u_walkthrough(data.get("walkthrough", {})) + _u_runs(data.get("execution", {}))),
        ("parts", "Load-bearing parts",
         "Some pieces carry more weight. Learn these first — a change here ripples everywhere.",
         _u_parts(data.get("key_parts", []))),
        ("start", "Your reading path", "Ready to read code? This order will make sense fastest.",
         _u_start(data.get("reading_path", {}))),
        ("coverage", "Blind spots",
         "Finally, an honest map of the edges — what we couldn't fully see, so you know where to be careful.",
         _u_coverage(data.get("coverage", {}))),
    ]
    total = len(chapters)
    secnav = "".join(f'<a class="viewtab" href="#{a}">{i + 1}. {_e(t)}</a>'
                     for i, (a, t, _tc, _in) in enumerate(chapters))
    secs = "".join(
        _u_chapter(i + 1, a, t, teach, inner,
                   (chapters[i + 1][0], chapters[i + 1][1]) if i + 1 < total else None)
        for i, (a, t, teach, inner) in enumerate(chapters))
    scrollspy = ("<script>(function(){var nav=document.querySelector('.understand-secnav');"
                 "if(!nav)return;var secs=[].slice.call(document.querySelectorAll('.u-sec'));"
                 "var links=[].slice.call(nav.querySelectorAll('a'));"
                 "var label=document.getElementById('u-progress');"
                 "var io=new IntersectionObserver(function(es){es.forEach(function(e){"
                 "if(e.isIntersecting){var i=secs.indexOf(e.target);"
                 "links.forEach(function(l,j){l.classList.toggle('active',j===i);});"
                 "if(label)label.textContent='Chapter '+(i+1)+' of '+secs.length;}});},"
                 "{rootMargin:'-45% 0px -50% 0px'});secs.forEach(function(s){io.observe(s);});})();</script>")
    body = f"""<div class="container">
  <h1>Understand <span class="tag">· {_e(name)}</span></h1>
  <p class="sub">A short guided lesson — how this codebase is organized and how it runs. Follow it top
    to bottom; every claim links into the same graph the tools use.</p>
  {_u_banner(enriched)}
  <div class="understand-secnav"><div class="viewtabs">{secnav}</div>
    <span id="u-progress" class="u-progress">Chapter 1 of {total}</span></div>
  {secs}
  {scrollspy}
</div>"""
    return layout("Understand", "/understand", body)


def _u_banner(enriched: bool) -> str:
    if enriched:
        return ("<p class='u-note'><span class='pill score' style='color:var(--green);"
                "border-color:var(--green)'>enriched</span> Purpose summaries &amp; business "
                "capabilities are active — the domain layer below is populated.</p>")
    return """<div class="card u-enrich">
    <div class="card-h">This is the structural view</div>
    <p class="muted">Everything below is derived from parsed structure alone — no LLM needed. Run the
      optional enrichment to add <b>purpose summaries</b> and <b>business capabilities</b> (the
      “what does this system do” layer), which light up <b>What&nbsp;&amp;&nbsp;why</b> and
      <b>Organized</b> below and the <a href="/flows">Flows</a> tab.</p>
    <div class="btnrow"><a class="btn primary" href="/enrich">Run enrichment →</a></div>
  </div>"""


def _u_chapter(n: int, anchor: str, title: str, teach: str, inner: str, nxt) -> str:
    if nxt:
        foot = f"<a class='u-next' href='#{nxt[0]}'>Next — {_e(nxt[1])} ↓</a>"
    else:
        foot = ("<p class='u-next'><b>That's the tour.</b> Go deeper: <a href='/graph'>Graph</a> · "
                "<a href='/flows'>Flows</a> · <a href='/search'>Search</a>.</p>")
    return (f"<section id='{anchor}' class='u-sec'><div class='u-chap'>"
            f"<span class='u-num'>{n}</span><div><h2>{_e(title)}</h2>"
            f"<p class='u-teach'>{_e(teach)}</p></div></div>{inner}{foot}</section>")


def _u_walkthrough(w: dict) -> str:
    if not w:
        return "<p class='muted small'>Not enough call structure to trace a concrete flow yet.</p>"
    entry, target = w["entry"], w["target"]
    steps = [f"<li><b>Execution starts</b> at {_kind_pill(entry['kind'])} "
             f"<a href='/graph?id={quote(entry['entity_id'])}'>{_e(entry['name'])}</a>.</li>"]
    if not w.get("same"):
        summ = f" <span class='muted small'>— {_e(target['summary'])}</span>" if target.get("summary") else ""
        steps.append(f"<li>It <b>{_e(w['verb'])}</b> "
                     f"{_symbol_link(target['qualified_name'], target['entity_id'], target['name'])}{summ}.</li>")
    elif target.get("summary"):
        steps.append(f"<li><span class='muted small'>{_e(target['summary'])}</span></li>")
    if w.get("calls"):
        links = ", ".join(_symbol_link(c['qualified_name'], c['entity_id'], c['name']) for c in w["calls"])
        steps.append(f"<li>{_e(target['name'])} <b>calls</b> {links}.</li>")
    if w.get("data"):
        tbls = ", ".join(f"<span class='mono'>{_e(d['name'])}</span> "
                         f"<span class='muted small'>({_e(d['access'])})</span>" for d in w["data"])
        steps.append(f"<li>Along the way it touches <b>data</b>: {tbls}.</li>")
    if w.get("capability"):
        steps.append(f"<li>All of this implements the <b>{_e(w['capability']['name'])}</b> "
                     f"capability — see it in <a href='/flows'>Flows</a>.</li>")
    return ("<div class='card u-walk'><div class='card-h'>Follow a thread</div>"
            "<p class='muted small'>One real path, traced end-to-end:</p>"
            f"<ol class='u-steps'>{''.join(steps)}</ol></div>")


def _u_what(summary: dict) -> str:
    totals = summary.get("totals", {})
    cards = "".join(
        f'<div class="card stat"><div class="n">{totals.get(k, 0)}</div><div class="l">{_e(k)}</div></div>'
        for k in ("files", "modules", "functions", "classes", "tests", "config_keys"))
    langs = "".join(f"<span class='pill'>{_e(k)} <span class='muted small'>{v}</span></span>"
                    for k, v in sorted(summary.get("languages", {}).items(), key=lambda kv: -kv[1]))
    purpose = summary.get("purpose", [])
    if purpose:
        blurb = "<div class='u-cardrow'>" + "".join(
            f"<div class='card'><div class='card-h'>{_e(p['name'])}</div>"
            f"<p class='muted small'>{_e(p['description'])}</p></div>" for p in purpose) + "</div>"
    else:
        blurb = ("<p class='muted small'>Run <a href='/enrich'>enrichment</a> to summarize what this "
                 "system does (business capabilities).</p>")
    inner = f"<div class='grid cards'>{cards}</div><p class='u-langs'>{langs}</p>{blurb}"
    return inner


def _u_arch_summary(org: dict) -> str:
    s = org.get("summary") or {}
    overview = s.get("overview")
    if not overview:
        return ""
    pts = "".join(f"<li>{_e(p)}</li>" for p in s.get("key_points", []))
    model = (s.get("llm") or {}).get("model", "llm")
    return (f"<div class='card u-arch'><div class='card-h'>The architect&rsquo;s read "
            f"<span class='tag'>\u00b7 {_e(model)}</span></div><p>{_e(overview)}</p>"
            + (f"<ul class='clean'>{pts}</ul>" if pts else "")
            + "<p class='muted small'>LLM-generated narrative grounded in the graph facts below "
            "\u00b7 not a verified fact.</p></div>")


def _u_organized(org: dict) -> str:
    layers = org.get("layers", [])
    edges = org.get("edges", [])
    if layers:
        n = len(layers)
        dep = (f", wired by <b>{len(edges)}</b> cross-layer dependenc{'y' if len(edges) == 1 else 'ies'}"
               if edges else "")
        items = "".join(
            f"<li><b>{_e(lyr['name'])}</b> — {_e(lyr.get('description') or 'a group of related modules')}</li>"
            for lyr in layers)
        lead = (f"<p>At a high level, this system is organized into <b>{n}</b> layer"
                f"{'' if n == 1 else 's'}{dep}. In plain terms:</p><ul class='clean'>{items}</ul>"
                "<p class='muted small' style='margin-top:10px'>The layers in detail:</p>")
    else:
        lead = ("<p class='muted'>No clear layering was inferred — likely a flat or single-purpose "
                "codebase.</p>")
    layer_cards = "".join(
        f"<div class='card'><div class='card-h'>{_e(lyr['name'])} "
        f"<span class='tag'>· {lyr.get('module_count', len(lyr.get('modules', [])))} modules</span></div>"
        f"<p class='muted small'>{_e(lyr.get('description', ''))}</p><ul class='clean'>" + "".join(
            f"<li><a href='/module?q={quote(m['qualified_name'])}'>{_e(m['qualified_name'])}</a>"
            + (f" — <span class='muted small'>{_e(m['summary'])}</span>" if m.get("summary")
               else f" <span class='muted small'>({m.get('symbols', 0)} symbols)</span>")
            + "</li>"
            for m in lyr.get("modules", [])[:5]) + "</ul></div>"
        for lyr in layers)
    caps = org.get("capabilities", [])
    if caps:
        strip = ("<p class='small' style='margin-top:14px'><b>Business capabilities</b> "
                 "<span class='muted'>(domain view)</span> · " + " ".join(
                     f"<span class='pill'>{_e(c['name'])} <span class='muted small'>{len(c['programs'])}</span></span>"
                     for c in caps[:10]) + " &nbsp;<a href='/flows'>open Flows →</a></p>")
    else:
        strip = ("<p class='muted small' style='margin-top:14px'>Domain grouping (business "
                 "capabilities) appears here once <a href='/enrich'>enrichment</a> runs.</p>")
    return f"{_u_arch_summary(org)}{lead}<div class='u-cardrow'>{layer_cards}</div>{strip}"


def _u_runs(ex: dict) -> str:
    ep = ex.get("entry_points", {}) or {}
    counts = " ".join(
        f"<span class='pill'>{_e(label)} <span class='muted small'>{ep.get(key, 0)}</span></span>"
        for key, label in (("jcl_jobs", "JCL jobs"), ("cics_transactions", "CICS txns"),
                           ("uncalled_programs", "uncalled programs"), ("python_main_guards", "__main__"))
        if ep.get(key)) or "<span class='muted small'>no distinct entry points detected</span>"
    mains = ex.get("mains", [])
    main_list = ("<ul class='clean'>" + "".join(
        f"<li><a href='/impact?q={quote(mn['qualified_name'])}'>{_e(mn['name'])}</a> "
        f"<span class='mono muted small'>{_e(mn['path'])}</span></li>" for mn in mains[:8]) + "</ul>") if mains else ""
    rows = "".join(
        f"<li><b>{_e(c['name'])}</b> <span class='muted small'>· triggered by "
        f"{', '.join(_e(t['name']) for t in c['triggers'][:4])}</span></li>"
        for c in ex.get("capabilities", [])[:8] if c.get("triggers"))
    cap_flows = f"<p class='small' style='margin-top:10px'><b>By capability</b></p><ul class='clean'>{rows}</ul>" if rows else ""
    return (f"<p>{counts} &nbsp; <a class='btn ghost small' href='/graph?view=entry_points'>"
            f"explore entry points →</a></p>{main_list}{cap_flows}")


def _u_part_row(h: dict) -> str:
    n = h.get("callers", 0)
    dep = f"{n} file{'' if n == 1 else 's'} depend on it"
    summ = f"<br><span class='muted small'>{_e(h['summary'])}</span>" if h.get("summary") else ""
    return (f"<li><a href='/impact?q={quote(h['qualified_name'])}'>{_e(h['name'])}</a> "
            f"{_kind_pill(h['kind'])} <span class='muted small'>{dep}</span> "
            f"<span class='mono muted small'>{_e(h['path'])}</span>{summ}</li>")


def _u_parts(hubs: list) -> str:
    if not hubs:
        return "<p class='muted small'>No cross-file hubs (nothing is depended on across files).</p>"
    return "<ul class='clean'>" + "".join(_u_part_row(h) for h in hubs[:12]) + "</ul>"


def _u_start(rp: dict) -> str:
    steps = "".join(
        f"<li><span class='step-n'>{s.get('order', '')}</span><b>{_e(s.get('title', ''))}</b> "
        f"<span class='mono muted small'>{_e(s.get('path', ''))}</span><br>"
        f"<span class='muted small'>{_e(s.get('why', ''))}</span></li>" for s in rp.get("steps", [])
    ) or "<li class='muted'>No reading path available.</li>"
    concepts = "".join(
        f"<li><b>{_e(c.get('layer', ''))}</b> — {_e(c.get('description', ''))}</li>"
        for c in rp.get("key_concepts", []))
    concepts_block = f"<h3>Key concepts</h3><ul class='clean'>{concepts}</ul>" if concepts else ""
    summary = f"<p class='muted small'>{_e(rp.get('summary', ''))}</p>" if rp.get("summary") else ""
    return f"{summary}<ul class='clean'>{steps}</ul>{concepts_block}"


def _u_coverage(cov: dict) -> str:
    gaps = cov.get("gaps", [])
    gap_block = (
        f"<div class='card'><div class='card-h'>Known unknowns <span class='tag'>· {cov.get('gap_count', 0)}</span></div>"
        "<p class='muted small'>Artifacts referenced in code but not indexed — the acquisition checklist.</p>"
        + ("<ul class='clean'>" + "".join(
            f"<li><span class='mono'>{_e(g['name'])}</span> {_kind_pill(g.get('artifact_kind', ''))} "
            f"<span class='muted small'>{g.get('ref_count', 0)} refs</span></li>" for g in gaps[:6]) + "</ul>"
           if gaps else "<p class='muted small'>None — every referenced artifact is indexed.</p>")
        + "<a class='btn ghost small' href='/gaps'>open Gaps →</a></div>")
    resolve_block = (
        "<div class='card'><div class='card-h'>Not fully resolved</div>"
        "<p class='muted small'>Call sites we could not statically pin down.</p><div class='btnrow'>"
        f"<span class='pill'>unresolved <span class='muted small'>{cov.get('unresolved_call_sites', 0)}</span></span>"
        f"<span class='pill'>dynamic dispatch <span class='muted small'>{cov.get('dynamic_call_sites', 0)}</span></span>"
        "<a class='btn ghost small' href='/metrics'>resolution scoreboard →</a></div></div>")
    shallow = cov.get("shallow_files", [])
    shallow_block = (
        f"<div class='card'><div class='card-h'>Parsed shallowly <span class='tag'>· {cov.get('shallow_files_total', 0)}</span></div>"
        "<p class='muted small'>Scanned but not parsed into structure (unknown language / no symbols).</p>"
        + ("<ul class='clean'>" + "".join(
            f"<li><span class='mono'>{_e(s['path'])}</span> <span class='muted small'>{_e(s['reason'])}</span></li>"
            for s in shallow[:8]) + "</ul>" if shallow else "<p class='muted small'>None.</p>") + "</div>")
    unused = cov.get("possibly_unused", [])
    unused_block = (
        f"<div class='card'><div class='card-h'>Possibly unused <span class='tag'>· {cov.get('possibly_unused_total', 0)}</span></div>"
        "<p class='u-caveat'>Heuristic — nothing in the repo references these. May include public API, "
        "dynamically-invoked, or framework code.</p>"
        + ("<ul class='clean'>" + "".join(
            f"<li>{_symbol_link(u['qualified_name'], u['entity_id'], u['name'])} {_kind_pill(u['kind'])} "
            f"<span class='mono muted small'>{_e(u['path'])}</span></li>" for u in unused[:10]) + "</ul>"
           if unused else "<p class='muted small'>None found.</p>") + "</div>")
    inner = f"<div class='u-cardrow'>{gap_block}{resolve_block}{shallow_block}{unused_block}</div>"
    return inner


def architecture_page(data: dict) -> str:
    layers = ""
    for layer in data.get("layers", []):
        mods = "".join(
            f"<li><a href='/module?q={quote(m['qualified_name'])}'>{_e(m['qualified_name'])}</a> "
            f"<span class='muted'>({m['symbols']} symbols)</span></li>"
            for m in layer["modules"][:8]
        )
        layers += f"""<div class="card" style="margin-bottom:14px">
          <h2 style="margin-top:0">{_e(layer['name'])} <span class='tag'>· {layer['module_count']} modules</span></h2>
          <p class="muted">{_e(layer['description'])}</p><ul class="clean">{mods}</ul></div>"""
    edges = "".join(
        f"<tr><td>{_e(e['source'])}</td><td>→</td><td>{_e(e['target'])}</td><td>{e['weight']}</td></tr>"
        for e in data.get("edges", [])
    ) or "<tr><td colspan=4 class='muted'>No cross-layer imports.</td></tr>"
    summary = data.get("summary") or {}
    overview_card = ""
    if summary.get("overview"):
        pts = "".join(f"<li>{_e(p)}</li>" for p in summary.get("key_points", []))
        model = (summary.get("llm") or {}).get("model", "llm")
        overview_card = f"""<div class="card" style="margin-bottom:14px">
      <h2 style="margin-top:0">System overview <span class='tag'>· {_e(model)}</span></h2>
      <p>{_e(summary['overview'])}</p>{f'<ul class="clean">{pts}</ul>' if pts else ''}
      <p class="muted small">LLM-generated narrative grounded in the graph facts below · not a verified fact.</p></div>"""
    body = f"""<div class="container">
  <h1>Architecture</h1>
  <p class="sub">Layers inferred from structure + the canonical graph.</p>
  {overview_card}
  <div class="split"><div>{layers}</div>
  <div><div class="card"><h2 style="margin-top:0">Layer dependencies</h2>
  <table><thead><tr><th>From</th><th></th><th>To</th><th>Imports</th></tr></thead><tbody>{edges}</tbody></table>
  </div></div></div>
</div>"""
    return layout("Architecture", "/architecture", body)


def gaps_page(data: dict) -> str:
    gaps = data.get("gaps", [])
    if not gaps:
        inner = "<p class='muted'>No gaps — every referenced artifact is indexed. 🎉</p>"
    else:
        parts = []
        for g in gaps:
            sites = ", ".join(f"{s['path']}:{s['line']}" for s in g["referencing_sites"][:5])
            parts.append(
                f"<tr><td class='mono'>{_e(g['name'])}</td><td>{_kind_pill(g['artifact_kind'])}</td>"
                f"<td>{g['ref_count']}</td><td class='mono muted'>{_e(g['expected_origin'])}</td>"
                f"<td class='mono muted'>{_e(sites)}</td></tr>"
            )
        inner = (
            "<table><thead><tr><th>Artifact</th><th>Kind</th><th>Refs</th>"
            "<th>Expected origin</th><th>Referencing sites</th></tr></thead>"
            f"<tbody>{''.join(parts)}</tbody></table>"
        )
    body = f"""<div class="container">
  <h1>Index gaps <span class="tag">· known unknowns</span></h1>
  <p class="sub">Artifacts referenced in the code but <b>not indexed</b>, ranked by references — the
    acquisition checklist. Each gap self-heals when its source is added. {len(gaps)} gap(s).</p>
  {inner}
</div>"""
    return layout("Gaps", "/gaps", body)


def onboarding_page(data: dict) -> str:
    steps = "".join(
        f"<li><span class='step-n'>{s['order']}</span><b>{_e(s['title'])}</b> "
        f"<span class='mono muted'>{_e(s['path'])}</span><br><span class='muted'>{_e(s['why'])}</span></li>"
        for s in data.get("steps", [])
    )
    concepts = "".join(
        f"<li><b>{_e(c['layer'])}</b> — {_e(c['description'])} <span class='muted'>({c['module_count']} modules)</span></li>"
        for c in data.get("key_concepts", [])
    )
    body = f"""<div class="container">
  <h1>Onboarding guide</h1>
  <p class="sub">{_e(data.get('summary'))}</p>
  <h2>Suggested reading order</h2><ul class="clean">{steps}</ul>
  <h2>Key concepts</h2><ul class="clean">{concepts}</ul>
</div>"""
    return layout("Onboarding", "/onboarding", body)


def _control_flow_section(cfg: dict | None) -> str:
    """A 'Control flow' block scheme on the symbol page. Renders as a diagram when mermaid.js is
    present (drop it into static/ for airgapped installs); otherwise shows the Mermaid source."""
    if not cfg or not cfg.get("ok"):
        return ""
    st = cfg.get("stats", {})
    tag = f"{st.get('decisions', 0)} decisions · {st.get('loops', 0)} loops · {st.get('nodes', 0)} blocks"
    notes = [n for n in cfg.get("nodes", []) if n.get("note")]
    notes_html = ""
    if notes:
        notes_html = "<ul class='clean small'>" + "".join(
            f"<li><span class='mono muted'>{_e(n['kind'])}</span> {_e(n['note'])}</li>"
            for n in notes) + "</ul>"
    return f"""<div style="margin-top:18px">
    <h2>Control flow <span class="tag">· {_e(tag)}</span></h2>
    <pre class="code mermaid">{_e(cfg['mermaid'])}</pre>
    {notes_html}
    <p class="muted small">Deterministic block scheme from source. Renders as a flowchart when
      <span class="mono">mermaid.js</span> is available; the Mermaid source is shown otherwise.</p>
    <script>if(window.mermaid){{try{{window.mermaid.initialize({{startOnLoad:true}});}}catch(e){{}}}}</script>
  </div>"""


def symbol_page(hit: dict, callers: list[dict], callees: list[dict], source: str,
                cfg: dict | None = None) -> str:
    def lst(items, empty):
        if not items:
            return f"<p class='muted'>{empty}</p>"
        return "<ul class='clean'>" + "".join(
            f"<li>{_symbol_link(h['qualified_name'], h['entity_id'], h['name'])} "
            f"<span class='mono muted'>{_e(h['path'])}</span></li>" for h in items
        ) + "</ul>"
    body = f"""<div class="container">
  <h1>{_e(hit['name'])} {_kind_pill(hit['kind'])}</h1>
  <p class="sub"><span class="mono">{_e(hit['qualified_name'])}</span> ·
    <span class="mono">{_e(hit['path'])}:{hit['start_line']}</span> ·
    <a href="/impact?q={quote(hit['qualified_name'])}">impact</a> ·
    <a href="/graph?id={quote(hit['entity_id'])}">graph</a></p>
  <div class="split">
    <div><h2>Source</h2><pre class="code">{_e(source) or '—'}</pre></div>
    <div><h2>Callers</h2>{lst(callers, 'None')}<h2>Callees</h2>{lst(callees, 'None')}</div>
  </div>
  {_control_flow_section(cfg)}
</div>"""
    return layout(hit["name"], "/", body)


__all__ = [
    "layout", "overview_page", "search_page", "graph_page", "impact_page",
    "module_page", "architecture_page", "flows_page", "understand_page", "onboarding_page", "gaps_page", "symbol_page",
]
