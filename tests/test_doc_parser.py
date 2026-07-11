from uci.core.entities import EntityType
from uci.parser.doc_parser import DocParser

MD = """\
# CardDemo Overview

CardDemo is a credit card system.

## Signon — COSGN00C

The `COSGN00C` program validates users against USRSEC.
See [the source](app/cbl/COSGN00C.cbl) and transaction CC00.

## Batch

Jobs run nightly.
"""


def _parse(text, path="README.md", lang="markdown", qname="README"):
    p = DocParser()
    p.language = lang
    return p.parse(text, path, qname)


def test_markdown_sections_with_line_spans():
    result = _parse(MD)
    secs = [s for s in result.symbols if s.kind is EntityType.DOC_SECTION]
    names = [s.name for s in secs]
    assert names == ["CardDemo Overview", "Signon — COSGN00C", "Batch"]
    signon = secs[1]
    assert signon.qualified_name == "README.signon-cosgn00c"
    assert signon.start_line == 5 and signon.end_line == 9
    assert signon.attributes["level"] == 2


def test_sections_never_raise_on_garbage():
    result = _parse("\x01\x02 not really markdown \n#\n###   \n")
    assert result.errors == [] or all(isinstance(e, str) for e in result.errors)


def test_headingless_doc_gets_one_whole_file_section():
    result = _parse("just prose\nmore prose\n", path="NOTES.txt", lang="doctext", qname="NOTES")
    secs = [s for s in result.symbols if s.kind is EntityType.DOC_SECTION]
    assert len(secs) == 1 and secs[0].name == "NOTES" and secs[0].start_line == 1
