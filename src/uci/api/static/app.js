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
            if (!merge) { nodes = []; edges = []; byId = {}; }
            const cx = canvas.width / (2 * devicePixelRatio), cy = canvas.height / (2 * devicePixelRatio);
            data.nodes.forEach((n) => {
                if (!byId[n.id]) {
                    n.x = cx + (Math.random() - 0.5) * 320;
                    n.y = cy + (Math.random() - 0.5) * 320;
                    n.vx = 0; n.vy = 0;
                    byId[n.id] = n; nodes.push(n);
                }
            });
            data.edges.forEach((e) => edges.push(e));
            ticks = 260;
        }

        let ticks = 260;
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
            const cx = canvas.width / (2 * devicePixelRatio), cy = canvas.height / (2 * devicePixelRatio);
            nodes.forEach((n) => {
                n.vx += (cx - n.x) * 0.0016; n.vy += (cy - n.y) * 0.0016;
                n.vx *= 0.86; n.vy *= 0.86;
                if (n !== dragging) { n.x += n.vx; n.y += n.vy; }
            });
        }

        function draw() {
            ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.save();
            ctx.translate(tx, ty); ctx.scale(scale, scale);
            ctx.strokeStyle = "rgba(139,152,169,.28)"; ctx.lineWidth = 1;
            edges.forEach((e) => {
                const a = byId[e.source], b = byId[e.target];
                if (!a || !b) return;
                ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
            });
            nodes.forEach((n) => {
                ctx.beginPath();
                ctx.arc(n.x, n.y, n.id === rootId ? 9 : 6, 0, Math.PI * 2);
                if (n.missing) {
                    ctx.setLineDash([3, 2]);
                    ctx.strokeStyle = "#f85149"; ctx.lineWidth = 1.5; ctx.stroke();
                    ctx.setLineDash([]);
                } else {
                    ctx.fillStyle = color(n.kind); ctx.fill();
                }
                ctx.fillStyle = n.missing ? "#f85149" : "#c9d3de";
                ctx.font = "11px ui-monospace, monospace";
                ctx.fillText((n.missing ? "⟂ " : "") + (n.name || ""), n.x + 9, n.y + 3);
            });
            ctx.restore();
        }

        function frame() { simulate(); draw(); requestAnimationFrame(frame); }

        function toWorld(mx, my) { return { x: (mx - tx) / scale, y: (my - ty) / scale }; }
        function pick(mx, my) {
            const w = toWorld(mx, my);
            return nodes.find((n) => (n.x - w.x) ** 2 + (n.y - w.y) ** 2 < 120);
        }
        canvas.addEventListener("mousedown", (ev) => {
            const r = canvas.getBoundingClientRect();
            const n = pick(ev.clientX - r.left, ev.clientY - r.top);
            if (n) { dragging = n; } else { panning = true; }
            last = { x: ev.clientX, y: ev.clientY };
        });
        window.addEventListener("mousemove", (ev) => {
            if (!last) return;
            const dx = ev.clientX - last.x, dy = ev.clientY - last.y;
            if (dragging) { dragging.x += dx / scale; dragging.y += dy / scale; ticks = Math.max(ticks, 30); }
            else if (panning) { tx += dx; ty += dy; }
            last = { x: ev.clientX, y: ev.clientY };
        });
        window.addEventListener("mouseup", () => {
            if (dragging) { const info = document.getElementById("node-info"); if (info) info.textContent = dragging.qualified_name || dragging.name; }
            dragging = null; panning = false; last = null;
        });
        canvas.addEventListener("dblclick", (ev) => {
            const r = canvas.getBoundingClientRect();
            const n = pick(ev.clientX - r.left, ev.clientY - r.top);
            if (n) load(n.id, true);
        });
        canvas.addEventListener("wheel", (ev) => {
            ev.preventDefault();
            const factor = ev.deltaY < 0 ? 1.1 : 0.9;
            scale = Math.max(0.2, Math.min(3, scale * factor));
        }, { passive: false });

        load(rootId, false);
        frame();
        window.__uciGraphLoad = (id) => load(id, false);
    }

    window.UCI = { initGraph };
})();
