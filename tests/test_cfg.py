"""Control-flow-graph builder + engine.control_flow (Tier-2 block scheme)."""

from __future__ import annotations

from pathlib import Path

from uci import Config, Engine
from uci.analysis.cfg import build_cobol_cfg, build_hlasm_cfg, build_python_cfg, narrate_cfg

_OVERDRAFT = '''
def post(balance, txns):
    total = 0
    for t in txns:
        if t < 0:
            if balance + t < 0:
                return "overdraft"
            balance += t
        else:
            balance += t
        total += t
    log(total)
    return "ok"
'''


def _edge_set(cfg):
    idx = {n.id: n for n in cfg.nodes}
    return {(idx[e.src].kind, e.label, idx[e.dst].kind) for e in cfg.edges}


def test_python_cfg_structure_of_a_branchy_loop():
    cfg = build_python_cfg(_OVERDRAFT, "post", "bank.py")
    st = cfg.stats()
    assert st["decisions"] == 2 and st["loops"] == 1 and st["returns"] == 2 and st["calls"] == 1
    edges = _edge_set(cfg)
    assert ("loop", "loop", "decision") in edges          # loop body starts with the if
    assert ("decision", "true", "decision") in edges      # nested if on the negative branch
    assert ("return", "", "exit") in edges                # returns flow to exit
    assert ("loop", "exit", "call") in edges              # loop exit → log(total)
    # a back-edge exists into the loop header from inside the body
    loop_id = next(n.id for n in cfg.nodes if n.kind == "loop")
    assert any(e.dst == loop_id and e.src != loop_id for e in cfg.edges)


def test_python_cfg_mermaid_has_shapes_and_labels():
    m = build_python_cfg(_OVERDRAFT, "post", "bank.py").to_mermaid()
    assert m.startswith("flowchart TD")
    assert '(["start"])' in m and '(["end"])' in m       # entry/exit stadium
    assert '{{"for t in txns"}}' in m                     # loop hexagon
    assert "-->|true|" in m and "-->|false|" in m         # branch labels


def test_python_cfg_missing_function_raises():
    try:
        build_python_cfg("def a():\n    pass\n", "nope", "x.py")
    except ValueError:
        return
    raise AssertionError("expected ValueError for a missing function")


def _py_repo(tmp_path: Path) -> Engine:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "acct.py").write_text(_OVERDRAFT + "\n\ndef log(x):\n    print(x)\n", encoding="utf-8")
    eng = Engine(Config.from_env(repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    return eng


def test_engine_control_flow_on_indexed_repo(tmp_path):
    eng = _py_repo(tmp_path)
    try:
        data = eng.control_flow("post")
        assert data["ok"] and data["language"] == "python"
        assert data["stats"]["decisions"] == 2 and data["stats"]["loops"] == 1
        assert data["mermaid"].startswith("flowchart TD")
    finally:
        eng.close()


def test_engine_control_flow_not_found(tmp_path):
    eng = _py_repo(tmp_path)
    try:
        data = eng.control_flow("does_not_exist")
        assert not data["ok"] and data["error"]["code"] == "not_found"
    finally:
        eng.close()


_COBOL = """       IDENTIFICATION DIVISION.
       PROGRAM-ID. POST.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM INIT-PARA.
           IF WS-FLAG = 'Y'
               MOVE 1 TO WS-CODE
           ELSE
               MOVE 0 TO WS-CODE
           END-IF.
           PERFORM CHECK-PARA UNTIL WS-DONE = 'Y'.
           GOBACK.
       INIT-PARA.
           MOVE 'N' TO WS-DONE.
       CHECK-PARA.
           ADD 1 TO WS-X.
"""


def test_cobol_cfg_structure():
    cfg = build_cobol_cfg(_COBOL, "POST", "post.cbl")
    st = cfg.stats()
    assert st["decisions"] == 1 and st["loops"] == 1 and st["returns"] == 1
    labels = {n.label for n in cfg.nodes}
    assert "MAIN-PARA" in labels and "INIT-PARA" in labels and "CHECK-PARA" in labels
    assert not any(n.label.startswith("END-") for n in cfg.nodes)  # scope terminators aren't nodes
    idx = {n.id: n for n in cfg.nodes}
    kinds = {(idx[e.src].kind, e.label, idx[e.dst].kind) for e in cfg.edges}
    assert ("call", "perform", "paragraph") in kinds       # PERFORM links to its paragraph
    assert ("decision", "true", "statement") in kinds       # IF forks to a statement
    assert ("return", "", "exit") in kinds                  # GOBACK → exit


def test_cobol_cfg_no_procedure_division_raises():
    try:
        build_cobol_cfg("       PROGRAM-ID. X.\n", "X", "x.cbl")
    except ValueError:
        return
    raise AssertionError("expected ValueError when there is no PROCEDURE DIVISION")


def test_engine_control_flow_on_cobol(tmp_path):
    repo = tmp_path / "cob"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cbl" / "POST.cbl").write_text(_COBOL, encoding="utf-8")
    eng = Engine(Config.from_env(repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    try:
        data = eng.control_flow("POST")
        assert data["ok"] and data["language"] == "cobol"
        assert data["stats"]["decisions"] == 1 and data["stats"]["loops"] == 1
        assert data["mermaid"].startswith("flowchart TD")
    finally:
        eng.close()


_HLASM = """SAMPLE   CSECT
         LA    R5,0
LOOP     C     R5,=F'10'
         BNL   DONE
         BAL   R14,PROCESS
         B     LOOP
DONE     BR    R14
PROCESS  AR    R6,R5
         BR    R14
         END
"""


def test_hlasm_cfg_basic_blocks_and_branches():
    cfg = build_hlasm_cfg(_HLASM, "SAMPLE", "sample.asm")
    st = cfg.stats()
    assert st["decisions"] == 1 and st["calls"] == 1 and st["returns"] == 2
    idx = {n.id: n for n in cfg.nodes}
    kinds = {(idx[e.src].kind, e.label, idx[e.dst].kind) for e in cfg.edges}
    assert ("decision", "taken", "return") in kinds     # BNL DONE → the DONE return block
    assert ("decision", "fall", "call") in kinds        # fall-through to the BAL block
    assert ("return", "", "exit") in kinds              # BR R14 → exit
    # the B LOOP back-edge makes a loop: some block branches back to the LOOP decision block
    loop_id = next(n.id for n in cfg.nodes if n.label.startswith("LOOP:"))
    assert any(e.dst == loop_id and e.label == "branch" for e in cfg.edges)


def test_hlasm_cfg_no_instructions_raises():
    try:
        build_hlasm_cfg("* just a comment\n", "X", "x.asm")
    except ValueError:
        return
    raise AssertionError("expected ValueError when there are no instructions")


# ------------------------------------------------------------------- narration
def _fake_complete_json(system, user, max_tokens=None):
    import json
    blocks = json.loads(user)["blocks"]
    return {"notes": [{"id": blocks[0]["id"], "note": "sets up the running total"},
                      {"id": "n-ghost", "note": "hallucinated block"}]}  # ghost must be dropped


def test_narrate_cfg_attaches_notes_and_drops_hallucinated_ids():
    cfg = build_python_cfg(_OVERDRAFT, "post", "bank.py")
    first = next(n for n in cfg.nodes if n.kind not in ("entry", "exit"))
    notes = narrate_cfg(cfg, _fake_complete_json)
    assert first.id in notes and "n-ghost" not in notes
    assert cfg.nodes[[n.id for n in cfg.nodes].index(first.id)].note == "sets up the running total"


class _FakeClient:
    default_tag = ""
    model = "fake"

    def complete_json(self, system, user, max_tokens=None):
        return _fake_complete_json(system, user, max_tokens)


def test_engine_control_flow_narrate_with_client(tmp_path):
    eng = _py_repo(tmp_path)
    try:
        data = eng.control_flow("post", narrate=True, client=_FakeClient())
        assert data["ok"] and data["narrated"] is True
        assert any(n.get("note") == "sets up the running total" for n in data["nodes"])
    finally:
        eng.close()
