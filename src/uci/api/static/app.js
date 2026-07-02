// UCI dashboard client: dependency-free canvas graph explorer + helpers.
(function () {
    const KIND_COLORS = {
        repository: "#8b98a9", directory: "#8b98a9", file: "#8b98a9", module: "#6ea8fe",
        package: "#8b98a9", function: "#4c8dff", method: "#4c8dff", class: "#7c5cff",
        interface: "#7c5cff", test: "#3fb950", config_key: "#d29922", variable: "#a0aec0",
        commit: "#d29922", author: "#3fb950", enum: "#7c5cff",
    };
    const color = (k) => KIND_COLORS[k] || "#8b98a9";

    function initGraph(rootId) {
        const canvas = document.getElementById("graph");
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        let nodes = [], edges = [], byId = {};
        let scale = 1, tx = 0, ty = 0, dragging = null, panning = false, last = null;
        let selected = null, ticks = 300, didFit = false, downAt = null;

        const vw = () => canvas.width / devicePixelRatio;
        const vh = () => canvas.height / devicePixelRatio;

        function resize() {
            const r = canvas.getBoundingClientRect();
            canvas.width = r.width * devicePixelRatio;
            canvas.height = r.height * devicePixelRatio;
        }
        window.addEventListener("resize", resize); resize();

        async function load(id, merge) {
            const res = await fetch("/api/graph?id=" + encodeURIComponent(id) + "&depth=1");
            const data = await res.json();
            if (!data.ok) return;
            if (!merge) { nodes = []; edges = []; byId = {}; didFit = false; }
            const cx = vw() / 2, cy = vh() / 2, base = nodes.length;
            data.nodes.forEach((n, i) => {
                if (!byId[n.id]) {
                    const a = (base + i) * 2.399963, rad = 40 + 15 * Math.sqrt(base + i);  // golden-angle spread
                    n.x = cx + Math.cos(a) * rad; n.y = cy + Math.sin(a) * rad;
                    n.vx = 0; n.vy = 0; byId[n.id] = n; nodes.push(n);
                }
            });
            data.edges.forEach((e) => edges.push(e));
            ticks = 300;
        }

        function simulate() {
            if (ticks <= 0) return;
            ticks--;
            const k = 0.02;
            for (let i = 0; i < nodes.length; i++) {
                const a = nodes[i];
                for (let j = i + 1; j < nodes.length; j++) {
                    const b = nodes[j];
                    let dx = a.x - b.x, dy = a.y - b.y;
                    let d2 = dx * dx + dy * dy + 0.01;
                    let f = 2400 / d2;
                    let d = Math.sqrt(d2);
                    a.vx += (dx / d) * f; a.vy += (dy / d) * f;
                    b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
                }
            }
            edges.forEach((e) => {
                const a = byId[e.source], b = byId[e.target];
                if (!a || !b) return;
                let dx = b.x - a.x, dy = b.y - a.y;
                let d = Math.sqrt(dx * dx + dy * dy) || 1;
                let f = (d - 90) * k;
                a.vx += (dx / d) * f; a.vy += (dy / d) * f;
                b.vx -= (dx / d) * f; b.vy -= (dy / d) * f;
            });
            const cx = vw() / 2, cy = vh() / 2;
            nodes.forEach((n) => {
                n.vx += (cx - n.x) * 0.0016; n.vy += (cy - n.y) * 0.0016;
                n.vx *= 0.86; n.vy *= 0.86;
                if (n !== dragging) { n.x += n.vx; n.y += n.vy; }
            });
            if (!didFit && ticks < 170 && nodes.length) { fit(); didFit = true; }
        }

        function fit() {
            if (!nodes.length) return;
            let a = Infinity, b = Infinity, c = -Infinity, d = -Infinity;
            nodes.forEach((n) => { a = Math.min(a, n.x); b = Math.min(b, n.y); c = Math.max(c, n.x); d = Math.max(d, n.y); });
            const pad = 70, w = (c - a) || 1, h = (d - b) || 1;
            scale = Math.max(0.15, Math.min(2.2, Math.min((vw() - pad) / w, (vh() - pad) / h)));
            tx = vw() / 2 - ((a + c) / 2) * scale;
            ty = vh() / 2 - ((b + d) / 2) * scale;
        }

        function zoomAt(mx, my, factor) {
            const wx = (mx - tx) / scale, wy = (my - ty) / scale;
            scale = Math.max(0.12, Math.min(4, scale * factor));
            tx = mx - wx * scale; ty = my - wy * scale;
        }

        function drawArrow(a, b, rB) {
            let dx = b.x - a.x, dy = b.y - a.y;
            const d = Math.sqrt(dx * dx + dy * dy) || 1, ux = dx / d, uy = dy / d;
            const ex = b.x - ux * (rB + 2), ey = b.y - uy * (rB + 2);
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(ex, ey); ctx.stroke();
            const ah = 6 / scale;
            ctx.beginPath();
            ctx.moveTo(ex, ey);
            ctx.lineTo(ex - ux * ah - uy * ah * 0.55, ey - uy * ah + ux * ah * 0.55);
            ctx.lineTo(ex - ux * ah + uy * ah * 0.55, ey - uy * ah - ux * ah * 0.55);
            ctx.closePath(); ctx.fillStyle = "rgba(139,152,169,.55)"; ctx.fill();
        }

        function draw() {
            ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
            ctx.clearRect(0, 0, vw(), vh());
            ctx.save();
            ctx.translate(tx, ty); ctx.scale(scale, scale);
            ctx.strokeStyle = "rgba(139,152,169,.30)"; ctx.lineWidth = 1 / scale;
            edges.forEach((e) => {
                const a = byId[e.source], b = byId[e.target];
                if (a && b) drawArrow(a, b, b.id === rootId ? 9 : 6);
            });
            const showLabels = scale > 0.5;
            nodes.forEach((n) => {
                const rad = n.id === rootId ? 9 : 6;
                ctx.beginPath();
                ctx.arc(n.x, n.y, rad, 0, Math.PI * 2);
                if (n.missing) {
                    ctx.setLineDash([3, 2]); ctx.strokeStyle = "#f85149"; ctx.lineWidth = 1.5 / scale; ctx.stroke(); ctx.setLineDash([]);
                } else {
                    ctx.fillStyle = color(n.kind); ctx.fill();
                }
                if (n === selected) {
                    ctx.beginPath(); ctx.arc(n.x, n.y, rad + 3, 0, Math.PI * 2);
                    ctx.strokeStyle = "#e6edf3"; ctx.lineWidth = 1.5 / scale; ctx.stroke();
                }
                if (showLabels || n === selected || n.id === rootId) {
                    ctx.fillStyle = n.missing ? "#f85149" : "#c9d3de";
                    ctx.font = (11 / scale) + "px ui-monospace, monospace";
                    ctx.fillText((n.missing ? "⟂ " : "") + (n.name || ""), n.x + rad + 3 / scale, n.y + 3 / scale);
                }
            });
            ctx.restore();
        }

        function frame() { simulate(); draw(); requestAnimationFrame(frame); }

        function toWorld(mx, my) { return { x: (mx - tx) / scale, y: (my - ty) / scale }; }
        function pick(mx, my) {
            const w = toWorld(mx, my), rr = 12 / scale;
            return nodes.find((n) => (n.x - w.x) ** 2 + (n.y - w.y) ** 2 < rr * rr);
        }
        function showInfo(n) {
            const info = document.getElementById("node-info");
            if (!info) return;
            const openable = ["function", "method", "class", "interface", "test", "module", "config_key", "enum"].includes(n.kind);
            const link = (openable && !n.missing) ? ' · <a href="/symbol?id=' + encodeURIComponent(n.id) + '">open ↗</a>' : "";
            info.innerHTML = (n.qualified_name || n.name || "") + link;
        }

        canvas.addEventListener("mousedown", (ev) => {
            const r = canvas.getBoundingClientRect();
            const n = pick(ev.clientX - r.left, ev.clientY - r.top);
            downAt = { x: ev.clientX, y: ev.clientY, moved: false };
            if (n) { dragging = n; selected = n; } else { panning = true; }
            last = { x: ev.clientX, y: ev.clientY };
        });
        window.addEventListener("mousemove", (ev) => {
            if (!last) return;
            const dx = ev.clientX - last.x, dy = ev.clientY - last.y;
            if (downAt && Math.abs(ev.clientX - downAt.x) + Math.abs(ev.clientY - downAt.y) > 3) downAt.moved = true;
            if (dragging) { dragging.x += dx / scale; dragging.y += dy / scale; ticks = Math.max(ticks, 24); }
            else if (panning) { tx += dx; ty += dy; }
            last = { x: ev.clientX, y: ev.clientY };
        });
        window.addEventListener("mouseup", () => {
            if (dragging && downAt && !downAt.moved) showInfo(dragging);  // a click (not a drag) shows details
            dragging = null; panning = false; last = null; downAt = null;
        });
        canvas.addEventListener("dblclick", (ev) => {
            const r = canvas.getBoundingClientRect();
            const n = pick(ev.clientX - r.left, ev.clientY - r.top);
            if (n) load(n.id, true);
        });
        canvas.addEventListener("wheel", (ev) => {
            ev.preventDefault();  // scroll or trackpad pinch -> zoom toward the cursor
            const r = canvas.getBoundingClientRect();
            zoomAt(ev.clientX - r.left, ev.clientY - r.top, Math.exp(-ev.deltaY * 0.0016));
        }, { passive: false });

        document.querySelectorAll("[data-graph]").forEach((btn) => btn.addEventListener("click", () => {
            if (btn.dataset.graph === "fit") return fit();
            zoomAt(vw() / 2, vh() / 2, btn.dataset.graph === "in" ? 1.25 : 0.8);
        }));

        load(rootId, false);
        frame();
        window.__uciGraphLoad = (id) => load(id, false);
    }

    window.UCI = { initGraph, initBuild, initEvals, initProjects };

    // --- shared job polling (Build + Evals) ---------------------------------
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    async function pollJob(jobId, onUpdate, interval) {
        interval = interval || 900;
        for (;;) {
            let data;
            try {
                const res = await fetch("/api/jobs/" + encodeURIComponent(jobId));
                data = await res.json();
            } catch (e) { await sleep(interval); continue; }
            if (!data.ok) return null;
            if (onUpdate) onUpdate(data.job);
            if (data.job.state !== "running") return data.job;
            await sleep(interval);
        }
    }

    function setState(el, state) {
        if (!el) return;
        el.textContent = state === "running" ? "running…" : state;
        el.className = "jobstate " + state;
    }
    function paintLog(pre, job) {
        if (!pre) return;
        pre.textContent = (job.log || []).join("\n");
        pre.scrollTop = pre.scrollHeight;
    }

    async function startJob(url, payload, logEl, stateEl, onDone) {
        setState(stateEl, "running");
        if (logEl) logEl.textContent = "";
        let data;
        try {
            const res = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload || {}),
            });
            data = await res.json();
        } catch (e) {
            setState(stateEl, "failed");
            if (logEl) logEl.textContent = "request failed: " + e;
            return;
        }
        if (!data.ok) {
            setState(stateEl, "failed");
            if (logEl) logEl.textContent = (data.error && data.error.message) || "failed to start";
            if (onDone) onDone(null);
            return;
        }
        const final = await pollJob(data.job.id, (job) => paintLog(logEl, job));
        if (final) { setState(stateEl, final.state); paintLog(logEl, final); }
        if (onDone) onDone(final);
    }

    function initBuild() {
        const logEl = document.getElementById("job-log");
        const stateEl = document.getElementById("job-state");
        const buttons = () => document.querySelectorAll("[data-build]");
        buttons().forEach((btn) => btn.addEventListener("click", () => {
            buttons().forEach((b) => (b.disabled = true));
            startJob("/api/build", { full: btn.dataset.build === "full" }, logEl, stateEl, () => {
                setTimeout(() => location.reload(), 700);  // reflect fresh index status
            });
        }));
        // re-attach to an in-flight build (e.g. after a reload)
        fetch("/api/jobs").then((r) => r.json()).then((d) => {
            const job = (d.jobs || []).find((j) => j.kind === "build" && j.state === "running");
            if (job) {
                setState(stateEl, "running");
                buttons().forEach((b) => (b.disabled = true));
                pollJob(job.id, (j) => paintLog(logEl, j)).then((f) => { if (f) setTimeout(() => location.reload(), 700); });
            }
        });
    }

    // --- evals --------------------------------------------------------------
    const CATS = ["symbol_lookup", "calls", "impact", "copybook_impact", "jobs",
        "transactions", "data_access", "queries", "completeness", "gaps"];
    const scoreClass = (v) => v == null ? "na" : (v >= 0.9 ? "good" : (v >= 0.5 ? "mid" : "bad"));
    const fmt = (v) => v == null ? "—" : (Math.round(v * 100) / 100).toFixed(2);
    const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

    function renderReport(report) {
        const wrap = document.getElementById("report-view");
        if (!wrap) return;
        const parts = [`<div class="mono muted small">${esc(report.run || "")} · ${esc(report.git_sha || "")}</div><div class="matrix">`];
        const tracks = report.tracks || {};
        Object.keys(tracks).forEach((tname) => {
            const t = tracks[tname], dsets = t.datasets || {};
            const present = new Set(); let hasCov = false;
            Object.values(dsets).forEach((d) => {
                Object.keys(d.categories || {}).forEach((c) => present.add(c));
                if (d.coverage != null) hasCov = true;
            });
            const cats = CATS.filter((c) => present.has(c));
            const head = cats.map((c) => `<th class="sc" title="${c}">${c.replace(/_/g, " ")}</th>`).join("") +
                (hasCov ? '<th class="sc">coverage</th>' : "");
            const rows = Object.entries(dsets).map(([name, d]) => {
                const cells = cats.map((c) => {
                    const cell = (d.categories || {})[c];
                    const v = cell ? cell.score : null;
                    const n = cell && cell.items != null ? cell.items : "?";
                    return `<td class="sc ${scoreClass(v)}" title="${c}: ${n} item(s)">${fmt(v)}</td>`;
                }).join("");
                const cov = hasCov ? `<td class="sc">${d.coverage != null ? (d.coverage * 100).toFixed(0) + "%" : "—"}</td>` : "";
                return `<tr><td>${esc(name)}</td><td class="sc">${(d.score || 0).toFixed(1)}</td>${cells}${cov}</tr>`;
            }).join("");
            parts.push(`<div class="trk">${esc(tname)} — ${(t.score || 0).toFixed(1)}/100</div>`);
            parts.push(`<table><thead><tr><th>dataset</th><th class="sc">score</th>${head}</tr></thead><tbody>${rows}</tbody></table>`);
            // per-dataset findings: what went well vs. what didn't
            Object.entries(dsets).forEach(([name, d]) => {
                const bad = Object.entries(d.categories || {}).filter(([, v]) => (v.score || 0) < 0.999);
                if ((d.score || 0) >= 99.95 && !bad.length) {
                    parts.push(`<div class="finding ok"><b>${esc(name)}</b> <span class="ok-tag">✓ clean</span> — all categories at 1.00</div>`);
                    return;
                }
                const badTxt = bad.map(([c, v]) => `${c.replace(/_/g, " ")} ${fmt(v.score)}`).join(", ");
                let h = `<div class="finding"><b>${esc(name)}</b> — ${(d.score || 0).toFixed(1)}/100`;
                if (badTxt) h += ` <span class="bad-tag">below 1.00: ${esc(badTxt)}</span>`;
                const notes = d.failures || [];
                if (notes.length) h += `<ul class="fail-list">${notes.slice(0, 40).map((f) => `<li>${esc(f)}</li>`).join("")}</ul>`;
                else h += ` <span class="muted small">(run with details to see per-item diffs)</span>`;
                parts.push(h + "</div>");
            });
        });
        parts.push("</div>");
        wrap.innerHTML = parts.join("");
    }

    function loadReport(name, rowEl) {
        document.querySelectorAll("#report-list tr[data-report]").forEach((r) => r.classList.remove("sel"));
        if (rowEl) rowEl.classList.add("sel");
        fetch("/api/evals/report?run=" + encodeURIComponent(name))
            .then((r) => r.json()).then((d) => { if (d.ok) renderReport(d.report); });
    }

    function bindReportRows() {
        document.querySelectorAll("#report-list tr[data-report]").forEach((row) =>
            row.addEventListener("click", () => loadReport(row.dataset.report, row)));
    }

    function refreshReports(openName) {
        fetch("/api/evals/reports").then((r) => r.json()).then((d) => {
            const tbody = document.getElementById("report-list");
            if (!tbody) return;
            tbody.innerHTML = (d.reports || []).map((r) => {
                const tracks = Object.entries(r.tracks || {}).map(([t, v]) =>
                    `<span class="pill score">${esc(t)} ${v == null ? "—" : Number(v).toFixed(1)}</span>`).join(" ");
                return `<tr data-report="${esc(r.name)}"><td>${r.baseline ? "★ " : ""}` +
                    `<span class="mono small">${esc(r.run || r.name)}</span></td><td>${tracks || "—"}</td></tr>`;
            }).join("") || "<tr><td colspan='2' class='muted'>No reports yet.</td></tr>";
            bindReportRows();
            if (openName) {
                const row = tbody.querySelector('tr[data-report="' + (window.CSS ? CSS.escape(openName) : openName) + '"]');
                loadReport(openName, row);
            }
        });
    }

    function initEvals() {
        const runBtn = document.getElementById("eval-run");
        const logEl = document.getElementById("job-log");
        const stateEl = document.getElementById("job-state");
        if (runBtn) runBtn.addEventListener("click", () => {
            const dataset = document.getElementById("eval-dataset").value;
            const baseline = document.getElementById("eval-baseline").checked;
            runBtn.disabled = true;
            startJob("/api/evals/run", { dataset, baseline }, logEl, stateEl, (job) => {
                runBtn.disabled = false;
                const rep = job && job.result && job.result.newest_report;
                setTimeout(() => refreshReports(rep && rep.name), 300);
            });
        });
        initEvalAuthoring();
        bindReportRows();
        fetch("/api/evals/reports").then((r) => r.json()).then((d) => {
            if (d.active) {
                setState(stateEl, "running");
                pollJob(d.active.id, (j) => paintLog(logEl, j)).then((f) => { if (f) refreshReports(); });
            }
        });
    }

    function initEvalAuthoring() {
        const createBtn = document.getElementById("eval-create");
        if (createBtn) createBtn.addEventListener("click", () => {
            const project = document.getElementById("eval-create-project").value;
            const name = (document.getElementById("eval-create-name").value || "").trim();
            const msg = document.getElementById("eval-create-msg");
            if (!project || !name) { if (msg) msg.textContent = "pick a project and a name"; return; }
            createBtn.disabled = true; if (msg) msg.textContent = "creating…";
            post("/api/evals/create", { project, name }).then((r) => r.json()).then((d) => {
                createBtn.disabled = false;
                if (!d.ok) { if (msg) msg.textContent = (d.error && d.error.message) || "failed"; return; }
                if (msg) msg.textContent = "created ‘" + d.name + "’ — reloading…";
                setTimeout(() => location.reload(), 700);
            });
        });

        const sel = document.getElementById("eval-edit-select");
        const text = document.getElementById("eval-edit-text");
        const msg = document.getElementById("eval-edit-msg");
        const load = (name) => {
            if (!name) return;
            fetch("/api/evals/dataset?name=" + encodeURIComponent(name)).then((r) => r.json()).then((d) => {
                if (d.ok) { text.value = JSON.stringify(d.dataset, null, 2); if (msg) msg.textContent = "loaded " + name; }
                else if (msg) msg.textContent = "not found";
            });
        };
        const loadBtn = document.getElementById("eval-edit-load");
        if (loadBtn) loadBtn.addEventListener("click", () => load(sel.value));
        if (sel) sel.addEventListener("change", () => load(sel.value));
        const saveBtn = document.getElementById("eval-edit-save");
        if (saveBtn) saveBtn.addEventListener("click", () => {
            const name = sel.value;
            if (!name) { if (msg) msg.textContent = "pick a dataset"; return; }
            let content;
            try { content = JSON.parse(text.value); }
            catch (e) { if (msg) msg.textContent = "invalid JSON: " + e.message; return; }
            saveBtn.disabled = true;
            post("/api/evals/dataset", { name, content }).then((r) => r.json()).then((d) => {
                saveBtn.disabled = false;
                if (msg) msg.textContent = d.ok ? "saved ✓" : ((d.error && d.error.message) || "save failed");
            });
        });
    }

    // --- auto-init on load (runs after this script is parsed; no inline calls needed) ---
    function post(url, payload) {
        return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}) });
    }

    function initProjects() {
        const addBtn = document.getElementById("proj-add");
        const logEl = document.getElementById("job-log");
        const stateEl = document.getElementById("job-state");
        if (addBtn) addBtn.addEventListener("click", () => {
            const path = (document.getElementById("proj-path").value || "").trim();
            if (!path) return;
            addBtn.disabled = true;
            post("/api/projects", { path }).then((r) => r.json()).then((d) => {
                if (!d.ok) { addBtn.disabled = false; alert((d.error && d.error.message) || "add failed"); return; }
                const nm = d.project && d.project.name;  // added — now index it (“Add & index”)
                if (nm) startJob("/api/build", { name: nm, full: true }, logEl, stateEl, () => location.reload());
                else location.reload();
            });
        });
        document.querySelectorAll("[data-activate]").forEach((b) => b.addEventListener("click", () =>
            post("/api/projects/activate", { name: b.dataset.activate }).then(() => location.reload())));
        document.querySelectorAll("[data-remove]").forEach((b) => b.addEventListener("click", () => {
            if (!confirm("Remove '" + b.dataset.remove + "' from the dashboard? (files are kept)")) return;
            post("/api/projects/remove", { name: b.dataset.remove }).then(() => location.reload());
        }));
        document.querySelectorAll("[data-index]").forEach((b) => b.addEventListener("click", () => {
            b.disabled = true;
            startJob("/api/build", { name: b.dataset.index, full: true }, logEl, stateEl,
                () => setTimeout(() => location.reload(), 700));
        }));
    }

    function boot() {
        const sw = document.getElementById("project-switcher");
        if (sw) sw.addEventListener("change", () =>
            post("/api/projects/activate", { name: sw.value }).then(() => location.reload()));
        const graph = document.getElementById("graph");
        if (graph) initGraph(graph.dataset.root || "");
        if (document.querySelector("[data-build]")) initBuild();
        if (document.getElementById("eval-run") || document.getElementById("report-list")) initEvals();
        if (document.getElementById("project-table")) initProjects();
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
})();
