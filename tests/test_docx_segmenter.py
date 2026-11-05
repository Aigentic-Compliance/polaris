import io

import pytest
from docx import Document

from polaris.classifier.docx_segmenter import extract_docx_preview, segment_docx
from polaris.common.exceptions import DOCXParsingError


def _make_real_policy_docx() -> bytes:
    doc = Document()
    doc.add_heading("Global Gifts and Hospitality Policy", level=1)
    doc.add_paragraph("Employees must not accept any gift exceeding EUR 50 in value.")
    doc.add_paragraph("All gifts must be registered within five business days of receipt.")
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Threshold"
    table.rows[0].cells[1].text = "EUR 50"
    table.rows[1].cells[0].text = "Approval required above"
    table.rows[1].cells[1].text = "EUR 25"

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_docx_with_header_footer() -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.header.paragraphs[0].text = "Acme Corp -- Confidential"
    section.footer.paragraphs[0].text = "Page footer text, not real content"
    doc.add_heading("Gifts Policy", level=1)
    doc.add_paragraph("Real policy content about gift thresholds.")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_docx_preview_uses_heading_style_as_title():
    docx_bytes = _make_real_policy_docx()
    title, body, page_count = extract_docx_preview(docx_bytes)
    assert title == "Global Gifts and Hospitality Policy"
    assert "EUR 50" in body
    assert page_count is None


def test_extract_docx_preview_falls_back_to_first_paragraph_when_no_heading():
    doc = Document()
    doc.add_paragraph("No heading style here, just a plain first paragraph.")
    doc.add_paragraph("Some more content.")
    buf = io.BytesIO()
    doc.save(buf)

    title, body, page_count = extract_docx_preview(buf.getvalue())
    assert title == "No heading style here, just a plain first paragraph."


def test_segment_docx_extracts_paragraphs_and_table_cells():
    docx_bytes = _make_real_policy_docx()
    segments = segment_docx(docx_bytes, source_id="test-docx-1")
    texts = [s.text for s in segments]
    assert "Employees must not accept any gift exceeding EUR 50 in value." in texts
    assert "All gifts must be registered within five business days of receipt." in texts
    assert "Threshold" in texts
    assert "EUR 50" in texts
    assert "Approval required above" in texts


def test_segment_docx_excludes_headers_and_footers():
    """Headers/footers live in python-docx's section.header/footer, never
    in doc.paragraphs -- this confirms segment_docx only reads the body,
    the DOCX equivalent of stripping <nav>/<footer> in html_segmenter.py."""
    docx_bytes = _make_docx_with_header_footer()
    segments = segment_docx(docx_bytes, source_id="test-docx-2")
    all_text = " ".join(s.text for s in segments)
    assert "Confidential" not in all_text
    assert "Page footer text" not in all_text
    assert "Real policy content about gift thresholds." in all_text


def test_segment_docx_deduplicates_identical_text():
    doc = Document()
    doc.add_paragraph("Standard disclaimer text.")
    doc.add_paragraph("Standard disclaimer text.")  # exact duplicate, e.g. repeated in two places
    buf = io.BytesIO()
    doc.save(buf)

    segments = segment_docx(buf.getvalue(), source_id="test-docx-3")
    matching = [s for s in segments if s.text == "Standard disclaimer text."]
    assert len(matching) == 1


def test_segment_docx_filters_very_short_fragments():
    doc = Document()
    doc.add_paragraph("Hi")  # below MIN_SEGMENT_CHARS
    doc.add_paragraph("A real substantive sentence about gift policy rules.")
    buf = io.BytesIO()
    doc.save(buf)

    segments = segment_docx(buf.getvalue(), source_id="test-docx-4")
    texts = [s.text for s in segments]
    assert "Hi" not in texts
    assert any("substantive sentence" in t for t in texts)


def test_corrupt_docx_raises_docx_parsing_error():
    with pytest.raises(DOCXParsingError, match="Could not open"):
        extract_docx_preview(b"this is not a real docx file at all")


def test_docx_segments_are_compatible_with_the_classification_cascade():
    """Same structural-compatibility regression this repo already hit
    once for HTML: cascade.classify_segment does `list(raw.bbox)`
    unconditionally, which would crash on a bare None bbox."""
    from polaris.classifier.cascade import classify_document

    docx_bytes = _make_real_policy_docx()
    segments = segment_docx(docx_bytes, source_id="test-compat-docx")
    result = classify_document(segments, source_id="test-compat-docx")
    assert len(result.segments) > 0
    assert len(result.failures) == 0
    assert result.segments[0].bbox == [0.0, 0.0, 0.0, 0.0]
    assert result.segments[0].page_from is None


def test_segment_docx_deduplicates_repeated_table_cell_text():
    doc = Document()
    table = doc.add_table(rows=2, cols=1)
    table.rows[0].cells[0].text = "Repeated value"
    table.rows[1].cells[0].text = "Repeated value"
    buf = io.BytesIO()
    doc.save(buf)

    segments = segment_docx(buf.getvalue(), source_id="test-docx-dup-cell")
    matching = [s for s in segments if s.text == "Repeated value"]
    assert len(matching) == 1
