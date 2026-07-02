"""Mainframe parser + graph integration tests (COBOL / JCL / CSD).

Covers the deterministic constructs from docs/lsp-refactoring-recommendations.md §3:
literal vs dynamic CALL, EXEC CICS XCTL/LINK, COPY resolution (internal / external / missing),
EXEC SQL includes + table access, JCL EXEC PGM= RUNS edges, CSD transaction INVOKES edges,
and the honesty contract (dynamic dispatch -> non-exact completeness; gap registry).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uci import Config, Engine

MAIN_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. MAINPGM.
      * a comment line with CALL 'GHOST' that must be ignored
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           COPY PAYCOPY.
           COPY DFHAID.
           COPY LOSTCOPY.
       01  WS-TARGET   PIC X(8) VALUE 'SUBPGM'.
       PROCEDURE DIVISION.
           CALL 'SUBPGM' USING WS-AREA.
           CALL 'CEE3ABD'.
           CALL 'NOWHERE' USING WS-AREA.
           CALL WS-TARGET USING WS-AREA.
           EXEC CICS XCTL
                     PROGRAM(WS-NEXT-PGM)
           END-EXEC.
           EXEC SQL
                SELECT BALANCE INTO :WS-BAL
                FROM STOCKTRD.CASHACC
                WHERE ID = :WS-ID
           END-EXEC.
           EXEC SQL
                UPDATE STOCKTRD.CASHACC SET BALANCE = :WS-BAL
           END-EXEC.
           EXEC SQL INCLUDE SQLCA END-EXEC.
           GOBACK.
"""

SUB_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. SUBPGM.
       PROCEDURE DIVISION.
           EXEC CICS LINK PROGRAM('MAINPGM') END-EXEC.
           GOBACK.
"""

PAY_CPY = """\
       01  PAY-RECORD.
           05  PAY-ID      PIC 9(8).
           05  PAY-AMOUNT  PIC 9(7)V99.
"""

RUN_JCL = """\
//NIGHTJOB JOB (ACCT),'NIGHTLY',CLASS=A
//* comment step
//STEP01   EXEC PGM=MAINPGM
//STEP02   EXEC PGM=IDCAMS
//SYSIN    DD *
//STEP03   EXEC PGM=&SYMPGM
"""

APP_CSD = """\
 DEFINE TRANSACTION(MN01) GROUP(TESTGRP)
        PROGRAM(MAINPGM) TWASIZE(0) STATUS(ENABLED)
 DEFINE TRANSACTION(SB01) GROUP(TESTGRP)
        PROGRAM(GONEPGM) TASKDATALOC(ANY)
 DEFINE FILE(ACCT) GROUP(TESTGRP)
        DSNAME(TEST.DATA) PROGRAM(NOTATRAN)
"""


@pytest.fixture
def mf_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mf"
    (repo / "cbl").mkdir(parents=True)
    (repo / "cpy").mkdir()
    (repo / "jcl").mkdir()
    (repo / "csd").mkdir()
    (repo / "cbl" / "MAINPGM.cbl").write_text(MAIN_CBL, encoding="utf-8")
    (repo / "cbl" / "SUBPGM.cbl").write_text(SUB_CBL, encoding="utf-8")
    (repo / "cpy" / "PAYCOPY.cpy").write_text(PAY_CPY, encoding="utf-8")
    (repo / "jcl" / "NIGHTJOB.jcl").write_text(RUN_JCL, encoding="utf-8")
    (repo / "csd" / "APP.csd").write_text(APP_CSD, encoding="utf-8")
    return repo


@pytest.fixture
def mf_engine(mf_repo: Path) -> Engine:
    eng = Engine(Config.from_env(mf_repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    yield eng
    eng.close()


def _names(results):
    return {r["name"].upper() for r in results}


# ---------------------------------------------------------------- programs & calls
def test_program_symbol_and_path(mf_engine):
    res = mf_engine.find_symbol("MAINPGM")["results"]
    assert res and res[0]["path"] == "cbl/MAINPGM.cbl"
    assert res[0]["kind"] == "legacy_program"


def test_literal_internal_call_is_syntactic(mf_engine):
    callees = mf_engine.callees("MAINPGM")["results"]
    sub = next(r for r in callees if r["name"].upper() == "SUBPGM")
    assert sub["resolution"] == "syntactic"


def test_cics_link_literal_resolves(mf_engine):
    callers = mf_engine.callers("MAINPGM")["results"]
    assert "SUBPGM" in _names(callers)


def test_external_api_call_labeled_external_not_gap(mf_engine):
    callees = mf_engine.callees("MAINPGM")["results"]
    cee = next(r for r in callees if r["name"].upper() == "CEE3ABD")
    assert cee["resolution"] == "external"
    gap_names = {g["name"] for g in mf_engine.gaps()["gaps"]}
    assert "CEE3ABD" not in gap_names


def test_missing_program_becomes_gap_with_stub_edge(mf_engine):
    gaps = {g["name"]: g for g in mf_engine.gaps()["gaps"]}
    assert "NOWHERE" in gaps
    assert gaps["NOWHERE"]["artifact_kind"] == "program"
    callees = mf_engine.callees("MAINPGM")["results"]
    nowhere = next(r for r in callees if r["name"].upper() == "NOWHERE")
    assert nowhere["resolution"] == "missing" and nowhere["missing"]


def test_dynamic_call_and_xctl_are_unresolved_not_edges(mf_engine):
    callees = _names(mf_engine.callees("MAINPGM")["results"])
    assert "WS-TARGET" not in callees and "WS-NEXT-PGM" not in callees
    imp = mf_engine.impact("MAINPGM")
    assert imp["completeness"]["level"] != "exact"
    assert imp["callees"]["unresolved"]["count"] >= 2


def test_static_program_completeness_exact(mf_engine):
    imp = mf_engine.impact("SUBPGM")
    assert imp["completeness"]["level"] == "exact"


# ---------------------------------------------------------------- copybooks
def test_copybook_impact_lists_dependent_program(mf_engine):
    res = mf_engine.find_symbol("PAYCOPY")["results"]
    assert res and res[0]["kind"] == "copybook"
    nb = mf_engine.graph_neighborhood(res[0]["entity_id"], depth=1, limit=100)
    # the IMPORTS edge lands on PAYCOPY's module entity, which shares the name
    names = {n["name"].upper() for n in nb["nodes"]}
    assert "MAINPGM" in names or _copy_importer_via_module(mf_engine)


def _copy_importer_via_module(engine) -> bool:
    for r in engine.find_symbol("PAYCOPY", exact=True)["results"]:
        nb = engine.graph_neighborhood(r["entity_id"], depth=1, limit=100)
        if any(n["name"].upper() == "MAINPGM" for n in nb["nodes"]):
            return True
    return False


def test_external_copybook_is_external_stub_not_gap(mf_engine):
    gap_names = {g["name"] for g in mf_engine.gaps()["gaps"]}
    assert "DFHAID" not in gap_names and "SQLCA" not in gap_names


def test_missing_copybook_is_gap(mf_engine):
    gaps = {g["name"]: g for g in mf_engine.gaps()["gaps"]}
    assert "LOSTCOPY" in gaps
    assert gaps["LOSTCOPY"]["artifact_kind"] == "copybook"
    assert gaps["LOSTCOPY"]["ref_count"] >= 1


# ---------------------------------------------------------------- SQL data access
def test_sql_reads_and_writes(mf_engine):
    data = mf_engine.find_data_lineage("MAINPGM")["results"]
    by_reason = {(r["name"].upper(), r["reason"].split()[0]) for r in data}
    assert ("CASHACC", "reads") in by_reason
    assert ("CASHACC", "writes") in by_reason


# ---------------------------------------------------------------- JCL
def test_jcl_job_runs_internal_program(mf_engine):
    res = mf_engine.find_symbol("NIGHTJOB")["results"]
    assert res and res[0]["kind"] == "jcl_job"
    nb = mf_engine.graph_neighborhood(res[0]["entity_id"], depth=1, limit=100)
    runs = [e for e in nb["edges"] if e["type"] == "runs"]
    target_names = {n["name"].upper() for n in nb["nodes"] if any(e["target"] == n["id"] for e in runs)}
    assert "MAINPGM" in target_names
    assert "IDCAMS" in target_names  # external stub, still an edge


def test_jcl_utility_is_external_and_symbolic_is_dynamic(mf_engine):
    gap_names = {g["name"] for g in mf_engine.gaps()["gaps"]}
    assert "IDCAMS" not in gap_names
    unresolved = mf_engine.impact("NIGHTJOB")["callees"]["unresolved"]
    assert unresolved["count"] >= 1  # PGM=&SYMPGM


# ---------------------------------------------------------------- CSD
def test_csd_transaction_invokes_program(mf_engine):
    res = mf_engine.find_symbol("MN01")["results"]
    assert res and res[0]["kind"] == "transaction_code"
    nb = mf_engine.graph_neighborhood(res[0]["entity_id"], depth=1, limit=50)
    invoked = {n["name"].upper() for n in nb["nodes"]
               if any(e["type"] == "invokes" and e["target"] == n["id"] for e in nb["edges"])}
    assert "MAINPGM" in invoked


def test_csd_missing_program_gap_and_file_defs_ignored(mf_engine):
    gaps = {g["name"] for g in mf_engine.gaps()["gaps"]}
    assert "GONEPGM" in gaps
    assert not mf_engine.find_symbol("ACCT")["results"]  # DEFINE FILE not a transaction
