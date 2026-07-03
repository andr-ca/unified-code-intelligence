"""Content-first language analysis: classify by content, extension only as a tiebreaker."""

from __future__ import annotations

from pathlib import Path

from uci.ingest.langanalyze import analyze_language
from uci.ingest.scanner import scan

COBOL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CBTRN02C.
       ENVIRONMENT DIVISION.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT        PIC 9(4) VALUE 0.
       PROCEDURE DIVISION.
           PERFORM 1000-INIT.
           MOVE 0 TO WS-COUNT.
"""

JCL = """\
//CBTRN02J JOB (ACCT),'CARD DEMO',CLASS=A,MSGCLASS=X
//STEP01   EXEC PGM=CBTRN02C
//STEPLIB  DD DSN=CARDDEMO.LOADLIB,DISP=SHR
//SYSOUT   DD SYSOUT=*
//SYSIN    DD *
"""

HLASM = """\
MYPROG   CSECT
         USING MYPROG,R12
         STM   R14,R12,12(R13)
FLAG     DC    C'Y'
COUNT    DS    F
         END   MYPROG
"""

BMS = """\
MAPSET1  DFHMSD TYPE=&SYSPARM,MODE=INOUT,LANG=COBOL
MAP1     DFHMDI SIZE=(24,80)
FIELD1   DFHMDF POS=(1,1),LENGTH=10
"""

PYTHON = """\
#!/usr/bin/env python3
import sys


def main():
    print("hi")


if __name__ == "__main__":
    main()
"""


def test_extensionless_members_detected_by_content():
    # a mainframe PDS member with no suffix is still classified from its content
    assert analyze_language("CBTRN02C", COBOL) == "cobol"
    assert analyze_language("CBTRN02J", JCL) == "jcl"
    assert analyze_language("MYPROG", HLASM) == "hlasm"
    assert analyze_language("MAPSET1", BMS) == "bms"
    assert analyze_language("runme", PYTHON) == "python"


def test_content_overrides_wrong_extension():
    # a COBOL program mislabeled as .txt is recognized as COBOL, not skipped/text
    assert analyze_language("weird/CBTRN02C.txt", COBOL) == "cobol"
    assert analyze_language("job.dat", JCL) == "jcl"


def test_extension_agrees_is_kept():
    assert analyze_language("app/cbl/CBTRN02C.cbl", COBOL) == "cobol"
    assert analyze_language("jcl/CBTRN02J.jcl", JCL) == "jcl"


def test_prose_with_incidental_marker_is_not_code():
    # a single incidental keyword in prose must not reclassify a document as code
    md = "# Design notes\n\nThis program has an IDENTIFICATION DIVISION and some tables.\n"
    assert analyze_language("notes.md", md) is None


def test_config_stays_extension_led():
    # config formats are not content-detected; the extension decides
    assert analyze_language("settings.json", '{"a": 1, "b": [1, 2]}') == "config"
    assert analyze_language("app.yaml", "name: demo\nport: 8080\n") == "config"


def test_empty_or_binary_falls_back_to_extension():
    assert analyze_language("x.py", "") == "python"     # empty -> extension
    assert analyze_language("blob", None) is None        # binary head -> no extension -> None


def test_scanner_rescues_extensionless_cobol(tmp_path: Path):
    from uci import Config

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "CBTRN02C").write_text(COBOL, encoding="utf-8")          # no extension
    (repo / "run.jcl").write_text(JCL, encoding="utf-8")             # normal extension
    (repo / "notes.md").write_text(
        "# Docs\nProse mentioning the PROCEDURE DIVISION concept once.\n", encoding="utf-8")
    scanned = {sf.rel_path: sf.language for sf in scan(Config.from_env(repo))}
    assert scanned.get("CBTRN02C") == "cobol"   # extensionless member rescued
    assert scanned.get("run.jcl") == "jcl"       # normal path unaffected
    assert "notes.md" not in scanned             # prose neither misclassified nor indexed
