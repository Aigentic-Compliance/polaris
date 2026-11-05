import pytest
import pymupdf as fitz

from polaris.classifier.document_dispatch import extract_preview_any, segment_any, sniff_document_type
from polaris.common.exceptions import DocumentParsingError


def _make_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Gifts Policy")
    page.insert_text((72, 100), "Employees must not accept gifts exceeding EUR 50.")
    return doc.tobytes()


def test_sniff_detects_pdf_by_magic_bytes():
    assert sniff_document_type(_make_pdf_bytes()) == "pdf"


def test_sniff_detects_html_by_doctype():
    assert sniff_document_type(b"<!DOCTYPE html><html><body>hi</body></html>") == "html"


def test_sniff_detects_html_without_doctype():
    assert sniff_document_type(b"<html><body>hi</body></html>") == "html"


def test_sniff_detects_html_with_leading_whitespace():
    assert sniff_document_type(b"   \n  <html><body>hi</body></html>") == "html"


def test_sniff_returns_unknown_for_neither():
    assert sniff_document_type(b"just some plain text, not a document at all") == "unknown"


def test_extract_preview_any_dispatches_to_pdf():
    title, body, pages, doc_type = extract_preview_any(_make_pdf_bytes())
    assert doc_type == "pdf"
    assert pages == 1
    assert "Gifts Policy" in title


def test_extract_preview_any_dispatches_to_html():
    html = b"<html><head><title>Gifts Policy</title></head><body><p>Employees must not accept gifts.</p></body></html>"
    title, body, pages, doc_type = extract_preview_any(html)
    assert doc_type == "html"
    assert pages is None
    assert title == "Gifts Policy"
    assert "Employees" in body


def test_extract_preview_any_raises_on_unrecognized_content():
    with pytest.raises(DocumentParsingError, match="neither recognizable PDF, HTML, nor DOCX"):
        extract_preview_any(b"totally unrecognizable garbage content")


def test_segment_any_dispatches_to_pdf_segmenter():
    segments = segment_any(_make_pdf_bytes(), "src-1", "pdf")
    assert len(segments) == 2


def test_segment_any_dispatches_to_html_segmenter():
    html = b"<html><body><p>First clause.</p><p>Second clause.</p></body></html>"
    segments = segment_any(html, "src-2", "html")
    assert len(segments) == 2


def test_segment_any_raises_on_unknown_document_type():
    with pytest.raises(ValueError, match="Unknown document_type"):
        segment_any(b"whatever", "src-3", "txt")  # "docx" is now a real, supported type -- use a genuinely unsupported one


def _make_docx_bytes() -> bytes:
    import io
    from docx import Document
    doc = Document()
    doc.add_paragraph("A real Word document paragraph about gifts.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_sniff_detects_docx_by_internal_manifest():
    assert sniff_document_type(_make_docx_bytes()) == "docx"


def test_sniff_does_not_confuse_a_plain_zip_for_docx():
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "just a plain zip, not a docx")
    assert sniff_document_type(buf.getvalue()) == "unknown"


def test_sniff_does_not_confuse_xlsx_for_docx():
    """xlsx and docx share the same outer ZIP magic bytes -- only the
    internal manifest (word/document.xml vs xl/workbook.xml) tells them
    apart. This confirms an Excel file is correctly reported as
    'unknown' (not yet supported), not misidentified as a Word document."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr("[Content_Types].xml", "<Types/>")
    assert sniff_document_type(buf.getvalue()) == "unknown"


def test_extract_preview_any_dispatches_to_docx():
    title, body, pages, doc_type = extract_preview_any(_make_docx_bytes())
    assert doc_type == "docx"
    assert pages is None
    assert "gifts" in body.lower()


def test_segment_any_dispatches_to_docx_segmenter():
    segments = segment_any(_make_docx_bytes(), "src-docx", "docx")
    assert len(segments) == 1
    assert "gifts" in segments[0].text.lower()


def test_sniff_handles_corrupt_zip_gracefully():
    """A truncated download that starts with ZIP magic bytes but isn't a
    complete/valid zip at all -- must report 'unknown', not crash."""
    corrupt_zip = b"PK\x03\x04" + b"not actually a complete valid zip structure"
    assert sniff_document_type(corrupt_zip) == "unknown"
