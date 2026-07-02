"""Server-rendered dashboard views (dependency-free HTML). All dynamic text is HTML-escaped."""

from __future__ import annotations

import html
from urllib.parse import quote

_NAV = [
    ("/", "Overview"),
    ("/search", "Search"),
    ("/graph", "Graph"),
    ("/architecture", "Architecture"),
    ("/metrics", "Metrics"),
    ("/gaps", "Gaps"),
    ("/onboarding", "Onboarding"),
    ("/build", "Build"),
    ("/projects", "Projects"),
    ("/config", "Config"),
    ("/enrich", "Enrich"),
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


def _nav_items() -> list[tuple[str, str]]:
    items = list(_NAV)
    if _SHOW_EVALS:
        items.append(("/evals", "Evals"))
    return items


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
    nav = "".join(
        f'<a href="{href}" class="{"active" if href == active else ""}">{_e(label)}</a>'
        for href, label in _nav_items()
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} · UCI</title>
<link rel="stylesheet" href="/static/app.css"></head>
<body>
<header class="topbar">
  <span class="brand"><b>UCI</b> · Unified Code Intelligence</span>
  <nav class="main">{nav}</nav>
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
        _cfg_select(cfg, ov, "llm_protocol", "Protocol", ("ollama", "openai", "anthropic")) +
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
    <b>dynamic-call candidates</b>, and <b>field dictionaries</b>. Every fact is labeled
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
    )
    return f"""<div class="card" style="margin-top:18px">
    <div class="card-h">Results — enrichment eval {honesty}</div>
    <table><thead><tr><th>pass</th><th class="sc">coverage / precision</th><th>detail</th></tr></thead>
    <tbody>{rows}</tbody></table>
    <p class="muted small">Honesty invariant: {hon.get('llm_suggested_edges', 0)} llm-suggested edges,
      {hon.get('leaked_into_ladder', 0)} leaked into the resolution ladder (must be 0). LLM facts stay
      in the candidate stratum at confidence &lt; 1.0, so multi-hop traversal and completeness never trust them.</p>
  </div>"""


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
    body = f"""<div class="container">
  <h1>Architecture</h1>
  <p class="sub">Layers inferred from structure + the canonical graph.</p>
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


def symbol_page(hit: dict, callers: list[dict], callees: list[dict], source: str) -> str:
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
</div>"""
    return layout(hit["name"], "/", body)


__all__ = [
    "layout", "overview_page", "search_page", "graph_page", "impact_page",
    "module_page", "architecture_page", "onboarding_page", "gaps_page", "symbol_page",
]
