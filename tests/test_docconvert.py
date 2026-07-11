import pytest

from uci.ingest.docconvert import available, extract_text


def test_unknown_language_unavailable():
    assert not available("markdown")   # only converter formats live here
    assert not available("nope")


def test_extract_text_returns_none_when_unavailable(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 fake")
    if available("pdf"):
        pytest.skip("pypdf installed; covered by test_extract_pdf_roundtrip")
    assert extract_text(str(p), "pdf", max_bytes=10_000_000) is None


@pytest.mark.optional_backend
def test_extract_docx_roundtrip(tmp_path):
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_heading("Payments Spec", level=1)
    doc.add_paragraph("COSGN00C validates users.")
    p = tmp_path / "spec.docx"
    doc.save(p)
    text = extract_text(str(p), "docx", max_bytes=10_000_000)
    assert "# Payments Spec" in text and "COSGN00C" in text


@pytest.mark.optional_backend
def test_extract_pdf_roundtrip(tmp_path):
    pytest.importorskip("pypdf")
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    p = tmp_path / "spec.pdf"
    c = reportlab.Canvas(str(p))
    c.drawString(72, 720, "COSGN00C signon program")
    c.showPage()
    c.save()
    text = extract_text(str(p), "pdf", max_bytes=10_000_000)
    assert text is not None and "[uci-page 1]" in text
