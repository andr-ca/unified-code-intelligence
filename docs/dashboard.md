# Dashboard (web UI)

`uci serve [--path REPO] [--host H] [--port P]` starts a **dependency-free** web dashboard on the
Python standard library (default `http://127.0.0.1:8765`, bound to localhost). Every view is a client
of the same canonical graph as the MCP/REST tools, so the dashboard and an agent can never disagree.

## Multiple projects — isolated at the DB level

Each registered project is indexed into its **own** database (`<repo>/.uci/uci.db`) — no cross-project
bleed. The registry lives at `$UCI_HOME/projects.json` (default `~/.uci/`). Switch the active project
from the **top-bar selector**; the **Projects** tab adds (by path), indexes, activates, and removes
them. `uci serve --path X` auto-registers `X` as the active project.

A registered-but-unindexed project (e.g. its `.uci` was cleaned) shows a one-click **“Index now”**
prompt instead of a broken/empty view.

## Tabs

### Explore
- **Overview** — totals, key symbols, modules, entry points.
- **Search** — graph-first hybrid search (symbol · keyword · semantic · graph · proximity · churn).
- **Graph** — offline canvas explorer. Pick an **angle** to seed the view from different starting
  points: *Repository* (whole tree), *Entry points* (downstream from mains / JCL jobs / CICS
  transactions / uncalled call-graph sources), *Most depended-on* (upstream from call-fan-in hubs),
  or *Modules*. Scroll or trackpad **pinch** to zoom toward the cursor, drag to pan, **Fit** / `±`
  controls, directed **arrows**. Click a node for an **info tile** — kind, location, **▲ called by**,
  **▼ calls**, with clickable entries to traverse; double-click a node to expand its neighborhood.
  Missing (stub) nodes are drawn dashed.
- **Architecture**, **Gaps** (known unknowns), **Onboarding** (dependency-ordered reading path).

### Metrics
Collected at index time: line stats per language (code / comment / blank / ratio), the
**call-resolution distribution** — the “% resolved” determinism scoreboard (syntactic / import-traced
/ inherited / inferred vs. name-match / candidate / external) — entry points, coupling
(cross-file / cross-directory edges), and top fan-in hubs. A project indexed before the metrics
feature shows a re-index prompt.

### Build
Re-index the active (or a named) project as a background **job** with live streamed logs; the index
status (generation, head sha, commits-behind) is shown alongside.

### Evals *(shown only when the UCI eval suite is present in the workspace)*
- **Run** the suite (all datasets or one), optionally gated against `evals/reports/baseline.json`.
- **Reports** rendered as a dataset × category matrix, each dataset marked **“✓ clean”** or listing
  its below-1.0 categories + per-item findings.
- **Create an eval from a project** — snapshot the project’s current extraction (symbols, resolved
  calls, queries, impact) into a golden dataset on the `custom` track, instantly runnable.
- **Edit a dataset** — **JSON** and human-readable **Readable** views; every save creates a new
  **version** (archived under `evals/datasets/.versions/<name>/`) with history + one-click **restore**.

### Config
Per-project settings — profile & backends, embeddings (provider / model / dims), ingest (gitignore /
all-text / file size / chunking), retrieval **weights** + RRF k, and gap external prefixes — saved to
`<repo>/.uci/overrides.json` and fed back to `Config.from_env(overrides=…)` on the next engine open.
Weights apply immediately; embedding / ingest changes need a **re-index**. Only changed fields are
pinned (badged *overridden*); **Reset** clears overrides.

## Concurrency & safety
`ThreadingHTTPServer` with a **per-project lock**; long operations run as background jobs so the UI
stays responsive (job-status polling is lock-free). Localhost-only; adding a project validates the
path; the eval runner is launched with a **fixed argv + dataset allowlist** (no shell, no injection);
dataset/version/report reads are path-traversal-safe.

## Selected JSON endpoints
`GET` `/api/overview` · `/api/search?q=` · `/api/graph?view=|id=` · `/api/entity?id=` · `/api/metrics`
· `/api/config` · `/api/projects` · `/api/evals/reports` · `/api/evals/report?run=` ·
`/api/evals/dataset|versions|version` · `/api/jobs[/<id>]`

`POST` `/api/build` · `/api/evals/run|create|dataset|restore` ·
`/api/projects[/activate|/remove]` · `/api/config` · `/api/mcp/call`
