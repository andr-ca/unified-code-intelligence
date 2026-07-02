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
    callees = mf_engine.callees("MAINPGM")["results"]
    names = _names(callees)
    assert "WS-TARGET" not in names and "WS-NEXT-PGM" not in names
    # CALL WS-TARGET is recovered by VALUE-clause dataflow (VALUE 'SUBPGM') at the R2 rung
    sub = next(r for r in callees if r["name"].upper() == "SUBPGM")
    assert sub["resolution"] in ("syntactic", "inferred")
    imp = mf_engine.impact("MAINPGM")
    # XCTL PROGRAM(WS-NEXT-PGM) has no literal reaching it -> still honest-unresolved
    assert imp["completeness"]["level"] != "exact"
    assert imp["callees"]["unresolved"]["count"] >= 1
    assert "WS-NEXT-PGM" in imp["callees"]["unresolved"]["names"]


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


def test_csd_missing_program_gap_and_file_becomes_dataset(mf_engine):
    gaps = {g["name"] for g in mf_engine.gaps()["gaps"]}
    assert "GONEPGM" in gaps
    res = mf_engine.find_symbol("ACCT")["results"]  # DEFINE FILE -> logical DATASET entity
    assert res and res[0]["kind"] == "dataset"


# ---------------------------------------------------------------- HLASM + DCLGEN
DATE_ASM = """\
*  DATE FORMATTER (LINKAGE FIXTURE)
DATEFMT  CSECT
         ENTRY DATEFMT2
         EXTRN LOGSVC
         CALL  TIMESVC
         L     R15,=V(CLOCKMOD)
         BALR  R14,R15
         COPY  ASMMACS
         END   DATEFMT
"""

CALLER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ASMCALLR.
       PROCEDURE DIVISION.
           CALL 'DATEFMT' USING WS-DATE.
           GOBACK.
"""

DCL_CPY = """\
           EXEC SQL DECLARE STOCKTRD.CASHACCT TABLE
           ( ACCT_ID        CHAR(8) NOT NULL,
             BALANCE        DECIMAL(11,2)
           ) END-EXEC.
       01  DCLCASHACCT.
           10 ACCT-ID       PIC X(8).
           10 BALANCE       PIC S9(9)V99 COMP-3.
"""


@pytest.fixture
def asm_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "asmrepo"
    (repo / "asm").mkdir(parents=True)
    (repo / "cbl").mkdir()
    (repo / "cpy").mkdir()
    (repo / "asm" / "DATEFMT.asm").write_text(DATE_ASM, encoding="utf-8")
    (repo / "cbl" / "ASMCALLR.cbl").write_text(CALLER_CBL, encoding="utf-8")
    (repo / "cpy" / "DCLCASH2.cpy").write_text(DCL_CPY, encoding="utf-8")
    return repo


@pytest.fixture
def asm_engine(asm_repo: Path) -> Engine:
    eng = Engine(Config.from_env(asm_repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    yield eng
    eng.close()


def test_hlasm_program_symbol_from_member(asm_engine):
    res = asm_engine.find_symbol("DATEFMT")["results"]
    assert res and res[0]["kind"] == "legacy_program"
    assert res[0]["path"] == "asm/DATEFMT.asm"


def test_cobol_call_to_asm_program_resolves_no_gap(asm_engine):
    callees = asm_engine.callees("ASMCALLR")["results"]
    date = next(r for r in callees if r["name"].upper() == "DATEFMT")
    assert date["resolution"] == "syntactic"
    assert "DATEFMT" not in {g["name"] for g in asm_engine.gaps()["gaps"]}


def test_hlasm_call_macro_and_vcon_produce_call_gaps(asm_engine):
    gaps = {g["name"]: g for g in asm_engine.gaps()["gaps"]}
    assert "TIMESVC" in gaps and "CLOCKMOD" in gaps  # named, not indexed -> acquisition list
    callees = {r["name"].upper() for r in asm_engine.callees("DATEFMT")["results"]}
    assert {"TIMESVC", "CLOCKMOD"} <= callees


def test_hlasm_extrn_becomes_depends_on(asm_engine):
    res = asm_engine.find_symbol("DATEFMT")["results"][0]
    nb = asm_engine.graph_neighborhood(res["entity_id"], depth=1, limit=100)
    dep_targets = {n["name"].upper() for n in nb["nodes"]
                   if any(e["type"] == "depends_on" and e["target"] == n["id"] for e in nb["edges"])}
    assert "LOGSVC" in dep_targets


def test_hlasm_copy_member_gap(asm_engine):
    gaps = {g["name"]: g for g in asm_engine.gaps()["gaps"]}
    assert "ASMMACS" in gaps and gaps["ASMMACS"]["artifact_kind"] == "copybook"


def test_dclgen_maps_to_table(asm_engine):
    res = asm_engine.find_symbol("DCLCASH2")["results"]
    assert res and res[0]["kind"] == "copybook"
    nb = asm_engine.graph_neighborhood(res[0]["entity_id"], depth=1, limit=50)
    mapped = {n["name"].upper() for n in nb["nodes"]
              if any(e["type"] == "maps_to" and e["target"] == n["id"] for e in nb["edges"])}
    assert "CASHACCT" in mapped
    lineage = asm_engine.find_data_lineage("DCLCASH2")
    assert any("CASHACCT" in (r.get("qualified_name") or "").upper()
               for r in lineage.get("results", []))


# ---------------------------------------------------------------- sprint: dataflow, VSAM, PROC, BMS
FLOW_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. FLOWPGM.
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT ACCT-FILE ASSIGN TO ACCTDD
               ORGANIZATION IS INDEXED.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-NEXT     PIC X(8).
       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT ACCT-FILE.
           MOVE 'SUBPGM' TO WS-NEXT.
           PERFORM SEND-SCREEN.
           EXEC CICS XCTL PROGRAM(WS-NEXT) END-EXEC.
       SEND-SCREEN.
           EXEC CICS SEND MAP('ACCTMAP') MAPSET('ACCTSET') END-EXEC.
           EXEC CICS READ FILE('ACCTDAT') INTO(WS-REC) END-EXEC.
           EXEC CICS
                CALL
           END-EXEC.
"""

ACCT_BMS = """\
ACCTSET  DFHMSD TYPE=&SYSPARM,MODE=INOUT,LANG=COBOL
ACCTMAP  DFHMDI SIZE=(24,80),LINE=1,COLUMN=1
         DFHMSD TYPE=FINAL
         END
"""

FLOW_PRC = """\
//DAILYPRC PROC
//PSTEP1   EXEC PGM=FLOWPGM
//INDD     DD DSN=PROD.ACCT.MASTER,DISP=SHR
//OUTDD    DD DSN=PROD.ACCT.REPORT,DISP=(NEW,CATLG)
"""

FLOW_JCL = """\
//DAILYJOB JOB (ACCT),'DAILY'
//RUNPROC  EXEC PROC=DAILYPRC
"""


@pytest.fixture
def flow_engine(tmp_path: Path) -> Engine:
    repo = tmp_path / "flowrepo"
    for d in ("cbl", "bms", "proc", "jcl"):
        (repo / d).mkdir(parents=True)
    (repo / "cbl" / "FLOWPGM.cbl").write_text(FLOW_CBL, encoding="utf-8")
    (repo / "cbl" / "SUBPGM.cbl").write_text(SUB_CBL, encoding="utf-8")
    (repo / "bms" / "ACCTSET.bms").write_text(ACCT_BMS, encoding="utf-8")
    (repo / "proc" / "DAILYPRC.prc").write_text(FLOW_PRC, encoding="utf-8")
    (repo / "jcl" / "DAILYJOB.jcl").write_text(FLOW_JCL, encoding="utf-8")
    eng = Engine(Config.from_env(repo, {"embedding_provider": "noop"}))
    eng.index(full=True)
    yield eng
    eng.close()


def test_move_dataflow_resolves_dynamic_xctl_as_inferred(flow_engine):
    callees = flow_engine.callees("FLOWPGM")["results"]
    sub = next((r for r in callees if r["name"].upper() == "SUBPGM"), None)
    assert sub is not None and sub["resolution"] == "inferred"


def test_vsam_open_and_cics_file_reads(flow_engine):
    data = flow_engine.find_data_lineage("FLOWPGM")["results"]
    names = {(r["name"].upper(), r["reason"].split()[0]) for r in data}
    assert ("ACCTDD", "reads") in names    # SELECT/ASSIGN + OPEN INPUT
    assert ("ACCTDAT", "reads") in names   # EXEC CICS READ FILE


def test_bms_screen_entity_and_uses_edge(flow_engine):
    res = flow_engine.find_symbol("ACCTMAP")["results"]
    assert res and res[0]["kind"] == "screen"
    nb = flow_engine.graph_neighborhood(res[0]["entity_id"], depth=1, limit=50)
    users = {n["name"].upper() for n in nb["nodes"]
             if any(e["type"] == "uses" and e["source"] == n["id"] for e in nb["edges"])}
    # the program's uses-edge must land on the BMS-defined map, not a duplicate stub
    assert "FLOWPGM" in {u for u in users} or any(
        e["type"] == "uses" for e in nb["edges"])


def test_paragraph_symbols_and_perform_edge(flow_engine):
    res = flow_engine.find_symbol("SEND-SCREEN")["results"]
    assert res and res[0]["kind"] == "paragraph"
    callers = flow_engine.callers("FLOWPGM.SEND-SCREEN")["results"]
    assert any(r["name"].upper() == "MAIN-PARA" for r in callers)


def test_prc_member_resolves_proc_gap_and_chains_to_program(flow_engine):
    gaps = {g["name"] for g in flow_engine.gaps()["gaps"]}
    assert "DAILYPRC" not in gaps  # the .prc member is indexed now
    res = flow_engine.find_symbol("DAILYPRC")["results"]
    assert res and res[0]["kind"] == "jcl_job"
    nb = flow_engine.graph_neighborhood(res[0]["entity_id"], depth=1, limit=100)
    runs_out = {n["name"].upper() for n in nb["nodes"]
                if any(e["type"] == "runs" and e["target"] == n["id"] for e in nb["edges"])}
    assert "FLOWPGM" in runs_out


def test_jcl_dd_dataset_edges_with_disp_heuristic(flow_engine):
    data = flow_engine.find_data_lineage("DAILYPRC")["results"]
    by = {(r["qualified_name"].upper(), r["reason"].split()[0]) for r in data}
    assert ("PROD.ACCT.MASTER", "reads") in by
    assert ("PROD.ACCT.REPORT", "writes") in by


# ---------------------------------------------------------------- code metrics
def test_code_metrics_collected(mf_engine):
    data = mf_engine.metrics()
    assert data["ok"]
    m = data["metrics"]
    assert m["files"] >= 5
    assert m["lines"]["code"] > 0 and m["lines"]["comment"] >= 1  # MAINPGM has a comment line
    assert m["by_language"]["cobol"]["files"] == 3  # 2 programs + 1 copybook
    assert m["by_language"]["jcl"]["files"] == 1
    ep = m["entry_points"]
    assert ep["jcl_jobs"] == 1            # NIGHTJOB
    assert ep["cics_transactions"] == 2   # MN01, SB01
    assert ep["total"] >= 3
    assert m["cross_dependencies"]["cross_file_edges"] > 0
    assert m["call_resolution_distribution"].get("syntactic", 0) >= 1
    assert m["dynamic_call_sites"] >= 1   # XCTL PROGRAM(WS-NEXT-PGM)
    assert m["missing_artifacts"] >= 2    # NOWHERE, LOSTCOPY, GONEPGM
    assert any(h["name"] == "MAINPGM" for h in m["top_fan_in"])


def test_metrics_line_classification():
    from uci.ingest.metrics import line_stats
    cobol = "       MOVE A TO B.\n      * comment line\n\n       GOBACK.\n"
    s = line_stats(cobol, "cobol")
    assert (s["code"], s["comment"], s["blank"]) == (2, 1, 1)
    py = "import os\n# comment\n\nx = 1\n"
    s = line_stats(py, "python")
    assert (s["code"], s["comment"], s["blank"]) == (2, 1, 1)
    jcl = "//JOB1 JOB\n//* note\n//S1 EXEC PGM=X\n"
    s = line_stats(jcl, "jcl")
    assert (s["code"], s["comment"]) == (2, 1)
