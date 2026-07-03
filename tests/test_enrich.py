"""LLM enrichment tests — deterministic fake client, no network (llm-enrichment.md §6)."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from uci import Config, Engine
from uci.core.relationships import RESOLVED_LEVELS
from uci.enrich.llm_client import LlmClient, LlmError
from uci.enrich.llm_logger import LlmCallLogger

PROG_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PRODINQ.
      * product inquiry: looks up supported products
       PROCEDURE DIVISION.
           EXEC SQL SELECT NAME FROM SHOP.PRODUCT_CATALOG END-EXEC.
           CALL 'PRODFMT' USING WS-REC.
           GOBACK.
"""

FMT_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. PRODFMT.
       PROCEDURE DIVISION.
           GOBACK.
"""

ROUTER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ROUTER.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-DISPATCH  PIC X(8).
       PROCEDURE DIVISION.
           MOVE MENU-PGM(WS-IDX) TO WS-DISPATCH.
           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC.
           GOBACK.
"""

DCL_CPY = """\
           EXEC SQL DECLARE SHOP.PRODUCT_CATALOG TABLE
           ( PROD_ID    CHAR(8) NOT NULL,
             PROD_NAME  VARCHAR(40)
           ) END-EXEC.
       01  DCLPRODCAT.
           10 PROD-ID    PIC X(8).
           10 PROD-NAME  PIC X(40).
"""


class FakeLlm:
    """Duck-typed LlmClient: canned deterministic answers keyed off the system prompt."""

    model = "fake-model"
    protocol = "fake"

    def __init__(self):
        self.calls = 0

    def describe(self):
        return {"protocol": "fake", "url": "-", "model": self.model, "api_key_set": False}

    def complete(self, system, user, max_tokens=None):
        self.calls += 1
        if "code analyst" in system:
            name = user.split("Artifact: ", 1)[1].split(" ", 1)[0]
            return f"Looks up supported products for {name} from the product catalog table."
        if "migration-readiness" in system:
            return "## Purpose\nProduct inquiry program.\n## Blast radius\nSee facts."
        return "ok"

    def complete_json(self, system, user, max_tokens=None):
        self.calls += 1
        if "business capabilities" in system:
            return [{"name": "Product Catalog", "description": "Product lookups",
                     "programs": ["PRODINQ", "PRODFMT", "NOTAREALPGM"]}]
        if "dynamic call site" in system:
            return {"candidates": ["PRODFMT", "GHOSTPGM"]}
        if "data structure" in system:
            return {"fields": [{"name": "PROD-ID", "meaning": "Product identifier"},
                               {"name": "PROD-NAME", "meaning": "Display name"}]}
        if "route a question" in system:
            return {"answer_location": "data",
                    "targets": [{"name": "SHOP.PRODUCT_CATALOG", "kind": "database_table",
                                 "why": "supported products are rows in this table"},
                                {"name": "HALLUCINATED.TBL", "kind": "database_table", "why": "x"}],
                    "explanation": "The product list is data, not code.",
                    "next_step": "Query the table."}
        if "system-architecture overview" in system:
            return {"overview": "A small COBOL product-inquiry system organized into a Core layer.",
                    "key_points": ["Core layer holds the programs", "PRODINQ looks up products"]}
        return {}


@pytest.fixture
def llm_repo(tmp_path: Path):
    repo = tmp_path / "llmrepo"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cpy").mkdir()
    (repo / "cbl" / "PRODINQ.cbl").write_text(PROG_CBL, encoding="utf-8")
    (repo / "cbl" / "PRODFMT.cbl").write_text(FMT_CBL, encoding="utf-8")
    (repo / "cbl" / "ROUTER.cbl").write_text(ROUTER_CBL, encoding="utf-8")
    (repo / "cpy" / "DCLPROD.cpy").write_text(DCL_CPY, encoding="utf-8")
    eng = Engine(Config.from_env(repo, {"embedding_provider": "local"}))
    eng.index(full=True)
    yield eng
    eng.close()


def test_enrich_summaries_boost_retrieval(llm_repo):
    fake = FakeLlm()
    baseline = [r["name"] for r in llm_repo.search("supported products lookup")["results"][:3]]
    out = llm_repo.enrich(["summaries"], client=fake)
    assert out["ok"] and out["stats"]["summaries"] >= 2
    ent = llm_repo.find_symbol("PRODINQ")["results"][0]
    detail = llm_repo.entity_detail(ent["entity_id"])
    # summary stored with LLM provenance + summary chunk indexed for retrieval
    chunk = llm_repo.metadata.get_chunk(f"summary:{ent['entity_id']}")
    assert chunk and "product" in chunk["text"].lower()
    results = [r["name"] for r in llm_repo.search("supported products lookup")["results"][:3]]
    assert "PRODINQ" in results
    assert results.index("PRODINQ") <= (baseline.index("PRODINQ") if "PRODINQ" in baseline else 99)


def test_enrich_summaries_cached_on_second_run(llm_repo):
    fake = FakeLlm()
    llm_repo.enrich(["summaries"], client=fake)
    first_calls = fake.calls
    out2 = llm_repo.enrich(["summaries"], client=fake)
    assert fake.calls == first_calls  # no new LLM calls
    assert out2["stats"]["cached"] >= 2


def test_enrich_capabilities_validated_against_index(llm_repo):
    out = llm_repo.enrich(["summaries", "capabilities"], client=FakeLlm())
    assert out["stats"]["capabilities"] == 1
    res = llm_repo.find_symbol("Product Catalog", exact=False)["results"]
    cap = next(r for r in res if r["kind"] == "business_capability")
    nb = llm_repo.graph_neighborhood(cap["entity_id"], depth=1, limit=50)
    members = {n["name"] for n in nb["nodes"]} - {"Product Catalog"}
    assert "PRODINQ" in members and "PRODFMT" in members
    assert "NOTAREALPGM" not in members  # hallucinated member discarded


def test_enrich_architecture_summary(llm_repo):
    fake = FakeLlm()
    out = llm_repo.enrich(["architecture"], client=fake)
    assert out["stats"]["architecture"] == 1
    # surfaced through engine.architecture() with llm provenance
    arch = llm_repo.architecture()
    summary = arch.get("summary")
    assert summary and "Core layer" in summary["overview"]
    assert summary["llm"]["pass"] == "architecture"
    assert summary["key_points"]
    # cached on second run — no new LLM call
    calls = fake.calls
    out2 = llm_repo.enrich(["architecture"], client=fake)
    assert fake.calls == calls and out2["stats"]["cached"] >= 1


def test_enrich_candidates_guardrails(llm_repo):
    out = llm_repo.enrich(["candidates"], client=FakeLlm())
    assert out["stats"]["candidate_edges"] == 1  # GHOSTPGM discarded
    callees = llm_repo.callees("ROUTER")["results"]
    cand = next(r for r in callees if r["name"] == "PRODFMT")
    assert cand["resolution"] == "llm-suggested"
    assert "llm-suggested" not in RESOLVED_LEVELS
    # honesty preserved: the dynamic site still keeps completeness non-exact
    imp = llm_repo.impact("ROUTER")
    assert imp["completeness"]["level"] != "exact"
    # and the edge sits in the candidates stratum, not resolved
    assert any(h["name"] == "PRODFMT" for h in imp["callees"]["candidates"])
    assert all(h["name"] != "PRODFMT" for h in imp["callees"]["resolved"])


def test_enrich_fields_data_dictionary(llm_repo):
    out = llm_repo.enrich(["fields"], client=FakeLlm())
    assert out["stats"]["field_dictionaries"] == 1
    cb = next(r for r in llm_repo.find_symbol("DCLPROD")["results"] if r["kind"] == "copybook")
    ent = llm_repo.graph.get_entity(cb["entity_id"])
    assert ent.attributes["data_dictionary"]["PROD-ID"] == "Product identifier"
    assert ent.attributes["llm"]["model"] == "fake-model"


def test_ask_routes_to_data_with_validated_targets(llm_repo):
    data = llm_repo.ask("what products are supported by the app?", client=FakeLlm())
    assert data["ok"] and data["answer_location"] == "data"
    names = [t["name"] for t in data["targets"]]
    assert "SHOP.PRODUCT_CATALOG" in names
    assert "HALLUCINATED.TBL" not in names  # unverifiable target dropped
    table = next(t for t in data["targets"] if t["name"] == "SHOP.PRODUCT_CATALOG")
    assert "PRODINQ" in table["read_by"]  # graph-proven reader attached


def test_briefing_renders_prose(llm_repo):
    data = llm_repo.briefing("PRODINQ", client=FakeLlm())
    assert data["ok"] and "Purpose" in data["briefing"]
    assert data["impact"]["target"]["name"] == "PRODINQ"


def test_llm_client_protocol_payloads(monkeypatch):
    captured = {}

    class FakeResp:
        def __init__(self, body): self._body = json.dumps(body).encode()
        def read(self): return self._body
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["payload"] = json.loads(req.data.decode())
        if "ollama" in captured["url"] or ":11434" in captured["url"]:
            return FakeResp({"message": {"content": "hi"}})
        if "chat/completions" in captured["url"]:
            return FakeResp({"choices": [{"message": {"content": "hi"}}]})
        return FakeResp({"content": [{"type": "text", "text": "hi"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    cfg = Config(llm_protocol="openai", llm_url="https://gw.example/v1", llm_model="m1",
                 settings={"llm_api_key": "sk-test"})
    assert LlmClient(cfg).complete("s", "u") == "hi"
    assert captured["url"] == "https://gw.example/v1/chat/completions"
    assert captured["headers"].get("Authorization") == "Bearer sk-test"
    assert captured["payload"]["model"] == "m1" and captured["payload"]["temperature"] == 0

    cfg = Config(llm_protocol="anthropic", llm_model="m2", settings={"llm_api_key": "ak"})
    assert LlmClient(cfg).complete("s", "u") == "hi"
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"].get("X-api-key") == "ak"
    assert captured["payload"]["system"] == "s"

    cfg = Config(llm_protocol="ollama")
    assert LlmClient(cfg).complete("s", "u") == "hi"
    assert captured["url"] == "http://localhost:11434/api/chat"

    with pytest.raises(LlmError):
        LlmClient(Config(llm_protocol="nope"))


def test_llm_logger_default_on_and_records_call(tmp_path):
    cfg = Config.from_env(str(tmp_path))
    lg = LlmCallLogger.from_config(cfg)
    assert lg is not None and lg.path == cfg.store_dir / "llm-calls.jsonl"
    lg.log(protocol="ollama", model="m", tag="summaries", max_tokens=100, latency_ms=42,
           ok=True, system="sys", user="u", response="r")
    rec = json.loads(lg.path.read_text().strip())
    assert rec["tag"] == "summaries" and rec["ok"] and rec["latency_ms"] == 42
    assert rec["response_chars"] == 1 and "error" in rec


def test_llm_logger_disabled_by_sentinel(tmp_path):
    assert LlmCallLogger.from_config(Config.from_env(str(tmp_path)).with_overrides(llm_log="off")) is None
    assert LlmCallLogger.from_config(Config.from_env(str(tmp_path)).with_overrides(llm_log="0")) is None


def test_llm_client_logs_every_completion_with_tag(tmp_path):
    log = tmp_path / "calls.jsonl"

    class C(LlmClient):
        def __init__(self, logfile):
            self.protocol, self.model = "fake", "m"
            self._logger = LlmCallLogger(logfile)
            self.default_tag = ""
            self.max_tokens = 100
        def _complete_raw(self, system, user, tokens):
            return "hello"

    c = C(log)
    c.default_tag = "capabilities"
    assert c.complete("s", "u") == "hello"          # uses default_tag
    assert c.complete("s", "u", tag="explicit") == "hello"  # explicit wins
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert [r["tag"] for r in rows] == ["capabilities", "explicit"]
    assert all(r["ok"] and r["response"] == "hello" for r in rows)


def test_llm_client_logs_failures_then_reraises(tmp_path):
    log = tmp_path / "calls.jsonl"

    class C(LlmClient):
        def __init__(self, logfile):
            self.protocol, self.model = "fake", "m"
            self._logger = LlmCallLogger(logfile)
            self.default_tag = "candidates"
            self.max_tokens = 100
        def _complete_raw(self, system, user, tokens):
            raise LlmError("boom")

    with pytest.raises(LlmError):
        C(log).complete("s", "u")
    rec = json.loads(log.read_text().strip())
    assert rec["ok"] is False and rec["error"] == "boom" and rec["tag"] == "candidates"


def test_llm_client_json_tolerates_fences():
    class C(LlmClient):
        def __init__(self): pass
        def complete(self, s, u, max_tokens=None, tag=""):
            return 'Here you go:\n```json\n{"a": 1}\n```\nthanks'
    assert C().complete_json("s", "u") == {"a": 1}


# ---------------------------------------------------------------- agentic tool-loop
class ScriptedLlm:
    """Returns a queued sequence of JSON actions (simulates a tool-using model)."""

    model = "scripted"
    protocol = "fake"

    def __init__(self, actions):
        self._actions = list(actions)
        self.seen = []

    def describe(self):
        return {"protocol": "fake", "url": "-", "model": self.model, "api_key_set": False}

    def complete_json(self, system, user, max_tokens=None):
        self.seen.append(user)
        return self._actions.pop(0) if self._actions else {"action": "answer", "candidates": []}


ROUTER_TL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. TLROUTER.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           COPY DISPTBL.
       01  WS-DISPATCH PIC X(8).
       PROCEDURE DIVISION.
           MOVE MENU-PGM(WS-IDX) TO WS-DISPATCH.
           EXEC CICS XCTL PROGRAM(WS-DISPATCH) END-EXEC.
           GOBACK.
"""
DISPTBL_TL = """\
       01  MENU-TABLE.
           05 FILLER PIC X(8) VALUE 'TLVIEW'.
           05 FILLER PIC X(8) VALUE 'TLEDIT'.
"""


@pytest.fixture
def tl_repo(tmp_path: Path):
    from uci import Config, Engine
    repo = tmp_path / "tlrepo"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cpy").mkdir()
    (repo / "cbl" / "TLROUTER.cbl").write_text(ROUTER_TL, encoding="utf-8")
    (repo / "cbl" / "TLVIEW.cbl").write_text("       PROGRAM-ID. TLVIEW.\n", encoding="utf-8")
    (repo / "cbl" / "TLEDIT.cbl").write_text("       PROGRAM-ID. TLEDIT.\n", encoding="utf-8")
    (repo / "cpy" / "DISPTBL.cpy").write_text(DISPTBL_TL, encoding="utf-8")
    eng = Engine(Config.from_env(repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    yield eng
    eng.close()


def test_tool_loop_pulls_file_then_answers(tl_repo):
    from uci.enrich.tool_loop import ToolLoop
    client = ScriptedLlm([
        {"action": "get_source", "path": "cpy/DISPTBL.cpy", "start": 1, "end": 40},
        {"action": "answer", "candidates": ["TLVIEW", "TLEDIT"]},
    ])
    loop = ToolLoop(client, tl_repo.graph, tl_repo.config.repo_path, tl_repo.repo_id)
    res = loop.run("system", "resolve WS-DISPATCH", "candidates")
    assert res.answer["candidates"] == ["TLVIEW", "TLEDIT"]
    assert res.tool_calls == 1
    # the copybook body was actually served into the conversation
    assert any("TLVIEW" in u for u in client.seen)


def test_tool_loop_budget_forces_answer(tl_repo):
    from uci.enrich.tool_loop import ToolLoop
    from uci.enrich.tool_loop import MAX_TOOL_CALLS
    # keeps requesting tools forever; harness must cap and force an answer
    client = ScriptedLlm([{"action": "search", "query": "X"}] * 10)
    loop = ToolLoop(client, tl_repo.graph, tl_repo.config.repo_path, tl_repo.repo_id)
    res = loop.run("system", "go", "candidates")
    assert res.tool_calls <= MAX_TOOL_CALLS
    assert res.answer.get("candidates", []) == []  # never got a real answer -> abstain


def test_tool_loop_clamps_path_to_repo(tl_repo):
    from uci.enrich.tool_loop import ToolLoop
    client = ScriptedLlm([{"action": "answer", "candidates": []}])
    loop = ToolLoop(client, tl_repo.graph, tl_repo.config.repo_path, tl_repo.repo_id)
    out = loop._get_source("../../../etc/passwd", 1, 5)
    assert out.startswith("error")


def test_agentic_candidates_resolves_cross_file(tl_repo):
    """The dispatch table is in a copybook the ±40-line seed window does not include."""
    from uci.enrich import Enricher

    class CrossFileLlm(ScriptedLlm):
        def complete_json(self, system, user, max_tokens=None):
            self.seen.append(user)
            if "TOOL RESULT" not in user:  # first turn: ask for the copybook
                return {"action": "get_source", "path": "cpy/DISPTBL.cpy", "start": 1, "end": 40}
            return {"action": "answer", "candidates": ["TLVIEW", "TLEDIT"]}

    enr = Enricher(tl_repo.config, tl_repo.graph, tl_repo.metadata, tl_repo.vectors,
                   tl_repo.embedder, tl_repo.repo_id, client=CrossFileLlm([]))
    enr.run(["candidates"], agentic=True)
    callees = tl_repo.callees("TLROUTER")["results"]
    names = {r["name"] for r in callees}
    assert {"TLVIEW", "TLEDIT"} <= names
    edge = next(r for r in callees if r["name"] == "TLVIEW")
    assert edge["resolution"] == "llm-suggested"
    # honesty invariant holds even with agentic evidence
    assert tl_repo.impact("TLROUTER")["completeness"]["level"] != "exact"


# ---------------------------------------------------------------- agentic ask (RAG follow-ups)
def test_tool_loop_rag_search_and_list_files(llm_repo):
    from uci.enrich.tool_loop import ToolLoop
    client = ScriptedLlm([
        {"action": "rag_search", "query": "supported products catalog"},
        {"action": "list_files", "prefix": "cpy/"},
        {"action": "get_source", "path": "cpy/DCLPROD.cpy", "start": 1, "end": 10},
        {"action": "answer", "done": True},
    ])
    loop = ToolLoop(client, llm_repo.graph, llm_repo.config.repo_path, llm_repo.repo_id,
                    retriever=llm_repo._retriever(), metadata=llm_repo.metadata,
                    max_tool_calls=4)
    res = loop.run("system", "find the product data", "done")
    assert res.tool_calls == 3 and res.answer.get("done") is True
    joined = " ".join(u for u in client.seen)
    assert "PRODINQ" in joined            # rag_search served hybrid hits
    assert "DCLPROD.cpy (cobol)" in joined  # list_files served the inventory
    assert "DECLARE SHOP.PRODUCT_CATALOG" in joined  # get_source served the copybook
    # tool list advertised in the protocol includes the optional tools
    assert "rag_search" in client.seen[0] and "list_files" in client.seen[0]


def test_tool_loop_optional_tools_absent_without_collaborators(llm_repo):
    from uci.enrich.tool_loop import ToolLoop
    client = ScriptedLlm([{"action": "answer", "done": True}])
    loop = ToolLoop(client, llm_repo.graph, llm_repo.config.repo_path, llm_repo.repo_id)
    loop.run("system", "go", "done")
    assert "rag_search" not in client.seen[0] and "list_files" not in client.seen[0]


def test_ask_agentic_routes_with_evidence(llm_repo):
    class AgenticAskLlm(ScriptedLlm):
        def complete_json(self, system, user, max_tokens=None):
            self.seen.append(user)
            if "TOOL RESULT" not in user:
                return {"action": "rag_search", "query": "product catalog table"}
            return {"action": "answer", "answer_location": "data",
                    "targets": [{"name": "SHOP.PRODUCT_CATALOG", "kind": "database_table",
                                 "why": "products are rows here"}],
                    "explanation": "Verified via follow-up search.",
                    "next_step": "Query the table."}

    data = llm_repo.ask("what products are supported?", client=AgenticAskLlm([]), agentic=True)
    assert data["ok"] and data["answer_location"] == "data"
    assert data["targets"][0]["name"] == "SHOP.PRODUCT_CATALOG"
    assert "PRODINQ" in data["targets"][0]["read_by"]   # graph-proven reader still attached
    assert data["evidence"]["tool_calls"] == 1
    assert data["evidence"]["tools_used"] == ["rag_search"]


def test_ask_agentic_degrades_honestly_on_unverifiable_targets(llm_repo):
    class HallucinatingLlm(ScriptedLlm):
        def complete_json(self, system, user, max_tokens=None):
            return {"action": "answer", "answer_location": "data",
                    "targets": [{"name": "TOTALLY.FAKE_TABLE", "kind": "database_table",
                                 "why": "made up"}],
                    "explanation": "x", "next_step": "y"}

    data = llm_repo.ask("what products are supported?", client=HallucinatingLlm([]), agentic=True)
    assert data["ok"] and data["answer_location"] == "not_in_repo"
    assert data["targets"] == []
