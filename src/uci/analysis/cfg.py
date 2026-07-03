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
    note: str = ""  # optional business-language annotation (LLM narration; never structural)


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


# ----------------------------------------------------------------- COBOL builder
# Verbs that begin a statement (used to find where an IF/EVALUATE condition ends and code starts).
_COBOL_VERBS = frozenset({
    "MOVE", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE", "COMPUTE", "DISPLAY", "ACCEPT",
    "PERFORM", "IF", "EVALUATE", "GO", "CALL", "GOBACK", "STOP", "EXIT", "OPEN", "CLOSE",
    "READ", "WRITE", "REWRITE", "DELETE", "START", "SET", "INITIALIZE", "STRING", "UNSTRING",
    "INSPECT", "SEARCH", "CONTINUE", "EXEC", "RETURN", "MERGE", "SORT", "RELEASE", "CANCEL",
    "NEXT",
})
_COBOL_SCOPE_ENDERS = frozenset({"END-IF", "END-EVALUATE", "END-PERFORM", "ELSE", "WHEN", "."})
_COBOL_LOOP_CLAUSES = frozenset({"UNTIL", "VARYING", "TIMES"})
_NOT_PARAGRAPH = frozenset({
    "PROCEDURE", "IDENTIFICATION", "ENVIRONMENT", "DATA", "DIVISION", "SECTION",
    "GOBACK", "STOP", "EXIT", "EJECT", "SKIP1", "SKIP2", "SKIP3", "DECLARATIVES",
})


def _cobol_tokens(lines: list[tuple[int, str]]) -> list[tuple[str, str, int]]:
    """Flatten ``(lineno, code)`` into ``(UPPER, orig, lineno)`` tokens; periods are their own token."""
    toks: list[tuple[str, str, int]] = []
    for lineno, code in lines:
        for word in code.replace(",", " ").replace(";", " ").split():
            core = word
            trailing = 0
            while core.endswith("."):
                core, trailing = core[:-1], trailing + 1
            if core:
                toks.append((core.upper(), core, lineno))
            toks.extend((".", ".", lineno) for _ in range(trailing))
    return toks


class _CobolCfgBuilder:
    """Statement-level CFG for a COBOL PROCEDURE DIVISION (or one paragraph). Targets well-structured
    code (explicit END-IF/END-EVALUATE/END-PERFORM). PERFORM of a paragraph is shown as a call block
    (subroutine semantics); GO TO is a control transfer to the target paragraph; fall-through links
    consecutive paragraphs, per COBOL semantics."""

    def __init__(self) -> None:
        self.nodes: list[CfgNode] = []
        self.edges: list[CfgEdge] = []
        self._seq = 0
        self.para_entry: dict[str, str] = {}
        self.pending_goto: list[tuple[str, str]] = []
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

    # -- top level ---------------------------------------------------------
    def build(self, proc_lines: list[tuple[int, str]]) -> None:
        segments = self._segment(proc_lines)
        # pre-create paragraph entry nodes so GO TO can target forward references
        prepared = []
        for name, lines, line0 in segments:
            nid = self._node("paragraph", name, line0) if name else None
            if name:
                self.para_entry[name.upper()] = nid
            prepared.append((nid, lines))
        cur: list[tuple[str, str]] = [(self.entry, "")]
        for nid, lines in prepared:
            if nid is not None:
                self._connect(cur, nid)
                cur = [(nid, "")]
            toks = _cobol_tokens(lines)
            exits, _ = self._statements(toks, 0, cur, terminators=frozenset())
            cur = exits
        self._connect(cur, self.exit)
        for from_id, target in self.pending_goto:
            self.edges.append(CfgEdge(from_id, self.para_entry.get(target.upper(), self.exit), ""))

    def _segment(self, lines: list[tuple[int, str]]):
        """Split procedure lines into (paragraph-name|None, lines, first-lineno). A paragraph header
        is a lone ``NAME.`` (or ``NAME SECTION.``) line; code before the first header is the None seg."""
        import re
        para_re = re.compile(r"^\s*([A-Z0-9][A-Z0-9-]*)\s*(SECTION)?\s*\.\s*$", re.IGNORECASE)
        segments: list[tuple[str | None, list[tuple[int, str]], int]] = []
        cur_name: str | None = None
        cur_lines: list[tuple[int, str]] = []
        cur_line0 = lines[0][0] if lines else 0
        for lineno, code in lines:
            m = para_re.match(code)
            name = m.group(1).upper() if m else ""
            if m and name not in _NOT_PARAGRAPH and not name.startswith("END-") \
                    and name not in _COBOL_VERBS:
                if cur_lines or cur_name is not None:
                    segments.append((cur_name, cur_lines, cur_line0))
                cur_name, cur_lines, cur_line0 = m.group(1), [], lineno
            else:
                cur_lines.append((lineno, code))
        if cur_lines or cur_name is not None:
            segments.append((cur_name, cur_lines, cur_line0))
        return segments

    # -- statement stream --------------------------------------------------
    def _statements(self, toks, i, preds, terminators):
        cur = preds
        while i < len(toks):
            v = toks[i][0]
            if v in terminators:
                break
            if v == ".":
                i += 1
                continue
            if v == "IF":
                cur, i = self._if(toks, i, cur)
            elif v == "EVALUATE":
                cur, i = self._evaluate(toks, i, cur)
            elif v == "PERFORM":
                cur, i = self._perform(toks, i, cur)
            elif v == "GO":
                cur, i = self._goto(toks, i, cur)
            elif v == "GOBACK" or (v == "STOP") or (v == "EXIT" and self._peek(toks, i + 1) == "PROGRAM"):
                cur, i = self._terminate(toks, i, cur)
            else:
                cur, i = self._simple(toks, i, cur)
        return cur, i

    def _peek(self, toks, i):
        return toks[i][0] if 0 <= i < len(toks) else ""

    def _collect(self, toks, i, stop):
        """Gather token text until a stop token/verb; returns (text, new_i)."""
        words = []
        while i < len(toks) and toks[i][0] not in stop and toks[i][0] not in _COBOL_VERBS:
            words.append(toks[i][1])
            i += 1
        return " ".join(words), i

    def _if(self, toks, i, preds):
        i += 1  # past IF
        cond, i = self._collect(toks, i, {"THEN", "."})
        if self._peek(toks, i) == "THEN":
            i += 1
        d = self._node("decision", f"IF {cond}", toks[i - 1][2])
        self._connect(preds, d)
        true_exits, i = self._statements(toks, i, [(d, "true")], {"ELSE", "END-IF", "."})
        if self._peek(toks, i) == "ELSE":
            i += 1
            false_exits, i = self._statements(toks, i, [(d, "false")], {"END-IF", "."})
        else:
            false_exits = [(d, "false")]
        if self._peek(toks, i) in ("END-IF", "."):
            i += 1
        return true_exits + false_exits, i

    def _evaluate(self, toks, i, preds):
        line = toks[i][2]
        i += 1  # past EVALUATE
        subj, i = self._collect(toks, i, {"WHEN", "."})
        d = self._node("decision", f"EVALUATE {subj}", line)
        self._connect(preds, d)
        exits: list[tuple[str, str]] = []
        while self._peek(toks, i) == "WHEN":
            i += 1
            cond, i = self._collect(toks, i, {"END-EVALUATE", "WHEN", "."})
            branch, i = self._statements(toks, i, [(d, f"when {cond}".strip())],
                                         {"WHEN", "END-EVALUATE", "."})
            exits += branch
        if self._peek(toks, i) in ("END-EVALUATE", "."):
            i += 1
        return (exits or [(d, "")]), i

    def _perform(self, toks, i, preds):
        line = toks[i][2]
        i += 1  # past PERFORM
        # look ahead within this sentence for END-PERFORM (inline) and loop clauses
        j, inline, is_loop, depth = i, False, False, 0
        while j < len(toks) and not (toks[j][0] == "." and depth == 0):
            t = toks[j][0]
            if t == "PERFORM":
                depth += 1
            elif t == "END-PERFORM":
                if depth == 0:
                    inline = True
                    break
                depth -= 1
            elif t in _COBOL_LOOP_CLAUSES:
                is_loop = True
            j += 1
        header, i = self._collect(toks, i, {"END-PERFORM", "."})
        if inline:
            kind = "loop" if is_loop else "statement"
            h = self._node(kind, f"PERFORM {header}".strip(), line)
            self._connect(preds, h)
            body, i = self._statements(toks, i, [(h, "loop" if is_loop else "")],
                                       {"END-PERFORM", "."})
            if self._peek(toks, i) == "END-PERFORM":
                i += 1
            if is_loop:
                self._connect(body, h)          # loop back
                return [(h, "exit")], i
            return body or [(h, "")], i
        # out-of-line: PERFORM para [THRU p2] [UNTIL/TIMES/VARYING …]
        para = header.split()[0] if header and header.split()[0].upper() not in _COBOL_LOOP_CLAUSES \
            else ""
        if not is_loop:
            node = self._node("call", f"PERFORM {header}".strip(), line)
            self._connect(preds, node)
            self._perform_edge(node, para)
            return [(node, "")], i
        # a loop: header + the performed paragraph as the body block that loops back
        h = self._node("loop", f"PERFORM {header}".strip(), line)
        self._connect(preds, h)
        if para:
            body = self._node("call", f"perform {para}", line)
            self.edges.append(CfgEdge(h, body, "loop"))
            self.edges.append(CfgEdge(body, h, ""))  # loop back
            self._perform_edge(body, para)
        return [(h, "exit")], i

    def _perform_edge(self, from_id: str, para: str) -> None:
        """Link a PERFORM to its paragraph (a call edge), so performed paragraphs stay reachable."""
        target = self.para_entry.get(para.upper())
        if target:
            self.edges.append(CfgEdge(from_id, target, "perform"))

    def _goto(self, toks, i, preds):
        line = toks[i][2]
        i += 1  # past GO
        if self._peek(toks, i) == "TO":
            i += 1
        target = toks[i][1] if i < len(toks) else ""
        i += 1
        # a DEPENDING-ON computed GO TO has multiple targets; take the first token as label
        g = self._node("goto", f"GO TO {target}", line)
        self._connect(preds, g)
        self.pending_goto.append((g, target))
        while i < len(toks) and toks[i][0] != ".":
            i += 1
        return [], i  # unconditional transfer: nothing falls through

    def _terminate(self, toks, i, preds):
        line = toks[i][2]
        label = toks[i][1].upper()
        if label == "STOP":
            label = "STOP RUN"
        elif label == "EXIT":
            label = "EXIT PROGRAM"
        r = self._node("return", label, line)
        self._connect(preds, r)
        self.edges.append(CfgEdge(r, self.exit, ""))
        while i < len(toks) and toks[i][0] != ".":
            i += 1
        return [], i

    def _simple(self, toks, i, preds):
        line = toks[i][2]
        verb = toks[i][0]
        words = [toks[i][1]]
        i += 1
        while i < len(toks) and toks[i][0] not in _COBOL_VERBS and toks[i][0] not in _COBOL_SCOPE_ENDERS:
            words.append(toks[i][1])
            i += 1
        kind = "call" if verb in ("CALL", "EXEC") else "statement"
        node = self._node(kind, " ".join(words), line)
        self._connect(preds, node)
        return [(node, "")], i


def build_cobol_cfg(source: str, symbol: str, path: str) -> ControlFlowGraph:
    """Build a CFG for a COBOL program's PROCEDURE DIVISION (statement-level, well-structured code)."""
    from ..parser.cobol_parser import _code_lines
    lines = list(_code_lines(source))
    start = next((k for k, (_, code) in enumerate(lines)
                  if "PROCEDURE" in code.upper() and "DIVISION" in code.upper()), None)
    if start is None:
        raise ValueError(f"no PROCEDURE DIVISION found in {path}")
    proc = lines[start + 1:]
    builder = _CobolCfgBuilder()
    builder.build(proc)
    return ControlFlowGraph(symbol=symbol, path=path, language="cobol",
                            nodes=builder.nodes, edges=builder.edges)


# ----------------------------------------------------------------- HLASM builder
# Basic-block CFG from raw assembler (macros/conditional assembly NOT expanded — that needs the
# Che4z LSP; docs/lsp-refactoring-recommendations.md §3.4). Branch families drive the edges.
_HL_UNCOND = frozenset({"B", "BRU", "J"})                          # unconditional branch to a label
_HL_COND = frozenset({
    "BE", "BNE", "BH", "BL", "BNH", "BNL", "BZ", "BNZ", "BP", "BM", "BO", "BNO", "BNP", "BNM",
    "BCT", "BCTR", "BXH", "BXLE",
    "JE", "JNE", "JH", "JL", "JNH", "JNL", "JZ", "JNZ", "JP", "JM", "JO", "JNO", "JCT",
})
_HL_CALL = frozenset({"BAL", "BALR", "BAS", "BASR", "BRAS", "BRASL", "JAS"})
_HL_RETURN_REGS = frozenset({"R14", "14", "R14,R15", "14,15"})
_HL_SECT = frozenset({"CSECT", "RSECT", "START"})


def _hlasm_instructions(source: str):
    """Yield ``(lineno, label, opcode, operand)`` for the first CSECT's body (macros unexpanded)."""
    started = False
    for i, raw in enumerate(source.splitlines(), start=1):
        if not raw.strip() or raw[:1] in ("*",) or raw.lstrip().startswith(".*"):
            continue
        line = raw[:72].rstrip()
        has_label = bool(line) and not line[0].isspace()
        parts = line.split()
        if not parts:
            continue
        label = parts[0] if has_label else ""
        rest = parts[1:] if has_label else parts
        opcode = rest[0].upper() if rest else ""
        operand = rest[1] if len(rest) > 1 else ""
        if opcode in _HL_SECT:
            if started:
                break            # next section → stop at first CSECT
            started = True
            continue
        if opcode == "END":
            break
        if started:
            yield (i, label, opcode, operand)


def _branch_target(operand: str, labels: set[str]) -> str | None:
    """The label a branch transfers to (last comma-separated operand token), if it's a real label."""
    tok = operand.split(",")[-1].strip().upper() if operand else ""
    return tok if tok in labels else None


def build_hlasm_cfg(source: str, symbol: str, path: str) -> ControlFlowGraph:
    """Basic-block CFG for an HLASM CSECT: blocks split at labels/after branches; edges from the
    branch family of each block's last instruction (fall-through, taken, call, return)."""
    instrs = list(_hlasm_instructions(source))
    if not instrs:
        raise ValueError(f"no assembler instructions found in {path}")
    labels = {lbl.upper() for _, lbl, _, _ in instrs if lbl}
    # leaders: first instr; any labeled instr; any instr after a branch/return
    leaders = {0}
    for k, (_, lbl, op, _) in enumerate(instrs):
        if lbl:
            leaders.add(k)
        if op in _HL_UNCOND or op in _HL_COND or op in _HL_CALL or op == "BR" or op == "BCR":
            if k + 1 < len(instrs):
                leaders.add(k + 1)
    order = sorted(leaders)
    blocks = [instrs[order[b]:order[b + 1] if b + 1 < len(order) else len(instrs)]
              for b in range(len(order))]

    b = _CfgCommon(symbol, path, "hlasm")
    label_block: dict[str, str] = {}
    block_nodes: list[str] = []
    for blk in blocks:
        lineno, lbl, _, _ = blk[0]
        last_op = blk[-1][2]
        kind = ("decision" if last_op in _HL_COND else
                "call" if last_op in _HL_CALL else
                "return" if last_op in ("BR", "BCR") and blk[-1][3].upper() in _HL_RETURN_REGS
                else "statement")
        text = lbl + ": " if lbl else ""
        text += " ".join(f"{op} {opnd}".strip() for _, _, op, opnd in blk[:3])
        nid = b.node(kind, text.strip() or "block", lineno)
        block_nodes.append(nid)
        if lbl:
            label_block[lbl.upper()] = nid
    b.connect([(b.entry, "")], block_nodes[0])

    for k, blk in enumerate(blocks):
        nid = block_nodes[k]
        last_op, last_operand = blk[-1][2], blk[-1][3]
        target = _branch_target(last_operand, labels)
        nxt = block_nodes[k + 1] if k + 1 < len(blocks) else b.exit
        if last_op in _HL_UNCOND and target:
            b.edge(nid, label_block.get(target, b.exit), "branch")
        elif last_op in _HL_COND and target:
            b.edge(nid, label_block.get(target, b.exit), "taken")
            b.edge(nid, nxt, "fall")
        elif last_op in _HL_CALL:
            if target:
                b.edge(nid, label_block.get(target, b.exit), "call")
            b.edge(nid, nxt, "")            # BAL/BALR returns → fall-through
        elif last_op in ("BR", "BCR") and last_operand.upper() in _HL_RETURN_REGS:
            b.edge(nid, b.exit, "")         # return via R14
        elif last_op in ("BR", "BCR"):
            b.edge(nid, b.exit, "computed")  # branch-to-register, untraceable target
        else:
            b.edge(nid, nxt, "")            # fall-through
    return ControlFlowGraph(symbol=symbol, path=path, language="hlasm",
                            nodes=b.nodes, edges=b.edges)


class _CfgCommon:
    """Small node/edge accumulator with an entry+exit, shared by the block-oriented builders."""

    def __init__(self, symbol: str, path: str, language: str) -> None:
        self.nodes: list[CfgNode] = []
        self.edges: list[CfgEdge] = []
        self._seq = 0
        self.entry = self.node("entry", "start", 0)
        self.exit = self.node("exit", "end", 0)

    def node(self, kind: str, label: str, line: int) -> str:
        nid = f"n{self._seq}"
        self._seq += 1
        self.nodes.append(CfgNode(nid, kind, label, line))
        return nid

    def edge(self, src: str, dst: str, label: str = "") -> None:
        self.edges.append(CfgEdge(src, dst, label))

    def connect(self, preds: list[tuple[str, str]], dst: str) -> None:
        for pid, label in preds:
            self.edges.append(CfgEdge(pid, dst, label))


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


_SYS_NARRATE = (
    "You annotate a control-flow graph with short business-language descriptions. You are given a "
    "routine's blocks (decisions, loops, calls, statements) and edges. Reply with STRICT JSON only: "
    "{\"notes\": [{\"id\": str, \"note\": str}]} where note is a <=12-word plain-English description "
    "of what that block does or decides. Use ONLY the block ids provided; do NOT invent blocks or "
    "control flow. Prioritise decisions and loops. Omit blocks that need no explanation."
)


def narrate_cfg(cfg: ControlFlowGraph, complete_json) -> dict[str, str]:
    """Attach optional business-language notes to CFG nodes via an injected ``complete_json`` callable
    (e.g. ``LlmClient.complete_json``). Narration is *labels only* — it can never change structure, and
    ids the model invents are dropped. Mutates ``cfg`` node ``note`` fields and returns the notes map."""
    import json
    payload = {
        "symbol": cfg.symbol, "language": cfg.language,
        "blocks": [{"id": n.id, "kind": n.kind, "label": n.label} for n in cfg.nodes
                   if n.kind not in ("entry", "exit")],
        "edges": [{"from": e.src, "to": e.dst, "label": e.label} for e in cfg.edges],
    }
    data = complete_json(_SYS_NARRATE, json.dumps(payload), max_tokens=600)
    ids = {n.id for n in cfg.nodes}
    notes: dict[str, str] = {}
    for item in (data.get("notes", []) if isinstance(data, dict) else []):
        if isinstance(item, dict) and item.get("id") in ids and str(item.get("note", "")).strip():
            notes[item["id"]] = str(item["note"]).strip()[:120]
    for n in cfg.nodes:
        n.note = notes.get(n.id, "")
    return notes


__all__ = ["ControlFlowGraph", "CfgNode", "CfgEdge", "build_python_cfg", "build_cobol_cfg",
           "narrate_cfg"]
