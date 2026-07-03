"""Control-flow graphs — the "logic inside" a routine as a block scheme (Tier-2).

A CFG is a deterministic, on-demand *analysis artifact* (like `walkthrough`/`architecture`), not
persisted graph entities — so it never bloats the canonical graph, and every node cites a source
line. Each language has its own builder; this module ships the **Python** builder (stdlib ``ast``,
fully faithful) and a language-agnostic model + Mermaid renderer. COBOL/others plug in the same
``ControlFlowGraph`` shape.

Node kinds: ``entry``/``exit`` (start/end), ``decision`` (if/match branch point), ``loop`` (while/
for header), ``call``, ``return``/``raise``, ``break``/``continue``, ``statement``. Edge labels carry
branch semantics (``true``/``false``/``loop``/``exit``/``case: …``) so the diagram reads as logic.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field


@dataclass
class CfgNode:
    id: str
    kind: str
    label: str
    line: int


@dataclass
class CfgEdge:
    src: str
    dst: str
    label: str = ""


@dataclass
class ControlFlowGraph:
    symbol: str
    path: str
    language: str
    nodes: list[CfgNode] = field(default_factory=list)
    edges: list[CfgEdge] = field(default_factory=list)

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for n in self.nodes:
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "decisions": kinds.get("decision", 0), "loops": kinds.get("loop", 0),
                "returns": kinds.get("return", 0), "calls": kinds.get("call", 0),
                "kinds": kinds}

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "path": self.path, "language": self.language,
                "nodes": [asdict(n) for n in self.nodes], "edges": [asdict(e) for e in self.edges],
                "stats": self.stats(), "mermaid": self.to_mermaid()}

    def to_mermaid(self) -> str:
        lines = ["flowchart TD"]
        for n in self.nodes:
            lines.append(f"  {n.id}{_shape(n.kind, n.label)}")
        for e in self.edges:
            arrow = f"-->|{_esc(e.label)}|" if e.label else "-->"
            lines.append(f"  {e.src} {arrow} {e.dst}")
        return "\n".join(lines)


def _esc(text: str) -> str:
    """Mermaid-safe label: collapse whitespace, cap length, neutralize quotes/brackets."""
    t = " ".join(str(text).split())[:60].replace('"', "'").replace("[", "(").replace("]", ")")
    return t or "…"


def _shape(kind: str, label: str) -> str:
    t = _esc(label)
    if kind in ("entry", "exit"):
        return f'(["{t}"])'          # stadium
    if kind == "decision":
        return f'{{"{t}"}}'          # rhombus
    if kind == "loop":
        return f'{{{{"{t}"}}}}'      # hexagon
    if kind == "call":
        return f'[["{t}"]]'          # subroutine
    if kind in ("return", "raise", "break", "continue"):
        return f'[/"{t}"/]'          # parallelogram
    return f'["{t}"]'                # rectangle


# ----------------------------------------------------------------- Python builder
class _PyCfgBuilder:
    """Threads a list of pending predecessors ``(node_id, edge_label)`` through the AST, connecting
    each statement to its predecessors and returning its exits — the standard AST→CFG construction."""

    def __init__(self) -> None:
        self.nodes: list[CfgNode] = []
        self.edges: list[CfgEdge] = []
        self._seq = 0
        self._loops: list[tuple[str, list[tuple[str, str]]]] = []  # (header_id, break_exits)
        self.entry = self._node("entry", "start", 0)
        self.exit = self._node("exit", "end", 0)

    def _node(self, kind: str, label: str, line: int) -> str:
        nid = f"n{self._seq}"
        self._seq += 1
        self.nodes.append(CfgNode(nid, kind, label, line))
        return nid

    def _connect(self, preds: list[tuple[str, str]], dst: str) -> None:
        for pid, label in preds:
            self.edges.append(CfgEdge(pid, dst, label))

    def build(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        exits = self._suite(fn.body, [(self.entry, "")])
        self._connect(exits, self.exit)  # normal fall-off the end → exit

    def _suite(self, stmts: list[ast.stmt], preds: list[tuple[str, str]]) -> list[tuple[str, str]]:
        cur = preds
        for stmt in stmts:
            cur = self._stmt(stmt, cur)
        return cur

    def _stmt(self, s: ast.stmt, preds: list[tuple[str, str]]) -> list[tuple[str, str]]:
        if isinstance(s, ast.If):
            d = self._node("decision", f"if {_src(s.test)}", s.lineno)
            self._connect(preds, d)
            true_exits = self._suite(s.body, [(d, "true")])
            false_exits = self._suite(s.orelse, [(d, "false")]) if s.orelse else [(d, "false")]
            return true_exits + false_exits

        if isinstance(s, (ast.While, ast.For, ast.AsyncFor)):
            head = "while " + _src(s.test) if isinstance(s, ast.While) \
                else f"for {_src(s.target)} in {_src(s.iter)}"
            h = self._node("loop", head, s.lineno)
            self._connect(preds, h)
            after: list[tuple[str, str]] = [(h, "exit")]  # condition-false / iterator-exhausted
            self._loops.append((h, after))
            body_exits = self._suite(s.body, [(h, "loop")])
            self._connect(body_exits, h)                  # loop back to the header
            self._loops.pop()
            return self._suite(s.orelse, after) if s.orelse else after

        if isinstance(s, ast.Return):
            r = self._node("return", "return " + _src(s.value) if s.value else "return", s.lineno)
            self._connect(preds, r)
            self.edges.append(CfgEdge(r, self.exit, ""))
            return []

        if isinstance(s, ast.Raise):
            r = self._node("raise", _src(s) or "raise", s.lineno)
            self._connect(preds, r)
            self.edges.append(CfgEdge(r, self.exit, ""))
            return []

        if isinstance(s, ast.Break):
            b = self._node("break", "break", s.lineno)
            self._connect(preds, b)
            if self._loops:
                self._loops[-1][1].append((b, ""))
            return []

        if isinstance(s, ast.Continue):
            c = self._node("continue", "continue", s.lineno)
            self._connect(preds, c)
            if self._loops:
                self.edges.append(CfgEdge(c, self._loops[-1][0], ""))
            return []

        if isinstance(s, ast.Match):
            d = self._node("decision", f"match {_src(s.subject)}", s.lineno)
            self._connect(preds, d)
            exits: list[tuple[str, str]] = []
            for case in s.cases:
                exits += self._suite(case.body, [(d, f"case {_src(case.pattern)}")])
            return exits or [(d, "")]

        if isinstance(s, (ast.With, ast.AsyncWith)):
            return self._suite(s.body, preds)  # a with-block is straight-line for flow purposes

        if isinstance(s, ast.Try):
            body_exits = self._suite(s.body, preds)
            handler_exits: list[tuple[str, str]] = []
            for h in s.handlers:
                htype = _src(h.type) if h.type else "…"
                handler_exits += self._suite(h.body, [(preds[0][0] if preds else self.entry,
                                                       f"except {htype}")])
            merged = self._suite(s.orelse, body_exits) if s.orelse else body_exits
            merged = merged + handler_exits
            return self._suite(s.finalbody, merged) if s.finalbody else merged

        # generic statement — call if it's a bare call expression, else a plain statement block
        kind = "call" if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) else "statement"
        node = self._node(kind, _src(s), s.lineno)
        self._connect(preds, node)
        return [(node, "")]


def _src(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - unparse is total on valid trees
        return type(node).__name__


def build_python_cfg(source: str, symbol: str, path: str, qualified_name: str = "") -> ControlFlowGraph:
    """Build a CFG for one function/method in ``source``. ``symbol`` is the (possibly dotted) name;
    the last segment selects the def, matched anywhere in the module (incl. methods in classes)."""
    tree = ast.parse(source)
    want = (qualified_name or symbol).split(".")[-1]
    target: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == want:
            target = node
            break
    if target is None:
        raise ValueError(f"function {want!r} not found in {path}")
    builder = _PyCfgBuilder()
    builder.build(target)
    return ControlFlowGraph(symbol=symbol, path=path, language="python",
                            nodes=builder.nodes, edges=builder.edges)


__all__ = ["ControlFlowGraph", "CfgNode", "CfgEdge", "build_python_cfg"]
