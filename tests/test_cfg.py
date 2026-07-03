"""Control-flow-graph builder + engine.control_flow (Tier-2 block scheme)."""

from __future__ import annotations

from pathlib import Path

from uci import Config, Engine
from uci.analysis.cfg import build_python_cfg

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
