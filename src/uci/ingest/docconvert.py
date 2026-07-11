"""Optional binary-document converters (PDF/DOCX -> text).

Adapters import their library lazily (pyproject extra ``docs``); a missing library means
``available() -> False`` and the scanner simply skips the file — never an error. Extracted
text is deterministic for a given file so the content-hash incremental path works unchanged.
DOCX headings are rendered as markdown ``#`` lines so the markdown section parser applies;
PDF pages are separated by an explicit marker line the doc parser turns into per-page sections.
"""

from __future__ import annotations

from pathlib import Path

PAGE_MARKER = "\f[uci-page {n}]"  # form-feed + marker line, one per PDF page


def available(language: str) -> bool:
    if language == "pdf":
        try:
            import pypdf  # noqa: F401
            return True
        except ImportError:
            return False
    if language == "docx":
        try:
            import docx  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def extract_text(abs_path: str, language: str, max_bytes: int) -> str | None:
    """Extract plain text, or ``None`` (unavailable converter, oversized, or extraction error)."""
    p = Path(abs_path)
    try:
        if p.stat().st_size > max_bytes:
            return None
    except OSError:
        return None
    if not available(language):
        return None
    try:
        if language == "pdf":
            return _pdf_text(p)
        if language == "docx":
            return _docx_text(p)
    except Exception:  # extraction must never break indexing
        return None
    return None


def _pdf_text(p: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(p))
    pages = []
    for n, page in enumerate(reader.pages, start=1):
        pages.append(PAGE_MARKER.replace("{n}", str(n)))
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _docx_text(p: Path) -> str:
    import docx
    doc = docx.Document(str(p))
    lines: list[str] = []
    for para in doc.paragraphs:
        style = (para.style.name or "").lower() if para.style else ""
        if style.startswith("heading"):
            try:
                level = max(1, min(6, int(style.rsplit(" ", 1)[-1])))
            except ValueError:
                level = 2
            lines.append(f"{'#' * level} {para.text}".rstrip())
        else:
            lines.append(para.text)
    return "\n".join(lines)


__all__ = ["available", "extract_text", "PAGE_MARKER"]
