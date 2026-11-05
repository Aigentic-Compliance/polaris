"""DOCX -> provenance-preserving segments -- the Word-document counterpart
to pdf_segmenter.py and html_segmenter.py.

Real corporate policies are frequently published as Word documents, not
just PDF or HTML -- this closes a real gap: discovery and download already
worked for any file type, but document_dispatch.py could only recognize
PDF or HTML, so a .docx candidate was downloaded successfully and then
rejected as an unrecognized format, the exact same shape of gap the HTML
fix addressed earlier in this build.

Segmentation strategy: one segment per non-empty paragraph, the same
paragraph-level granularity as PDF and HTML. Headers and footers
(python-docx exposes these separately per section, distinct from the
document body) are explicitly excluded -- they're navigation/branding
chrome, the direct equivalent of PDF's running-header filter and HTML's
stripped <nav>/<footer> tags. Table cell text is included (an approval
matrix or a threshold table is exactly the kind of content this needs to
see), each cell as its own segment.

Failure modes: a corrupt or non-.docx zip raises DOCXParsingError (a
DocumentParsingError subclass, same shared base as PDFParsingError and
HTMLParsingError, so callers that catch the common base don't need a
third specific except clause).
"""

from __future__ import annotations

from dataclasses import dataclass

from docx import Document

from polaris.common.exceptions import DOCXParsingError
from polaris.common.logging_config import get_logger

logger = get_logger(__name__)

MIN_SEGMENT_CHARS = 3  # matches pdf_segmenter/html_segmenter's boilerplate length floor


@dataclass
class RawDocxSegment:
    """Structurally compatible with pdf_segmenter.RawSegment (text,
    page_from, page_to, bbox) for the same reason RawHTMLSegment is --
    cascade.classify_segment reads exactly those four attributes and does
    `list(raw.bbox)` unconditionally. page/bbox have no meaning for DOCX
    (no fixed pagination or coordinates in the file format itself)."""
    text: str
    page_from: int | None = None
    page_to: int | None = None
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


def _open(docx_bytes: bytes) -> Document:
    import io
    try:
        return Document(io.BytesIO(docx_bytes))
    except Exception as exc:
        # python-docx can raise several different exception types for a
        # malformed file -- PackageNotFoundError, zipfile.BadZipFile (the
        # actual exception for "not a zip at all"), or an XML parsing
        # error for a valid zip with corrupt internal XML. All of them
        # mean the same thing to a caller: this could not be read as a
        # .docx, full stop -- caught broadly here so every failure mode
        # surfaces as the same DOCXParsingError, not three different
        # exception types callers would need to know to catch separately.
        logger.error("failed to open .docx (%d bytes): %s", len(docx_bytes), exc)
        raise DOCXParsingError(f"Could not open .docx ({len(docx_bytes)} bytes): {exc}") from exc


def extract_docx_preview(docx_bytes: bytes, max_chars: int = 4000) -> tuple[str, str, None]:
    """Mirrors pdf_segmenter.extract_preview / html_segmenter.extract_html_preview's
    role: a cheap preview for document_validator.py. Title is the first
    Heading-styled paragraph if one exists, else the first non-empty
    paragraph -- Word documents commonly use built-in heading styles for
    a document's own title, a stronger signal than "just the first line."
    page_count is always None -- pagination in a .docx is a rendering-time
    concept (depends on the viewer's page size/margins), not something the
    file itself defines, unlike a PDF's fixed page count.
    """
    doc = _open(docx_bytes)

    title_text = ""
    for para in doc.paragraphs:
        if para.style and para.style.name and para.style.name.lower().startswith("heading") and para.text.strip():
            title_text = para.text.strip()
            break
    if not title_text:
        for para in doc.paragraphs:
            if para.text.strip():
                title_text = para.text.strip()
                break

    body_parts = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
    body_text = " ".join(body_parts)
    return title_text[:2000], body_text[:max_chars], None


def segment_docx(docx_bytes: bytes, source_id: str) -> list[RawDocxSegment]:
    doc = _open(docx_bytes)
    segments: list[RawDocxSegment] = []
    seen_texts: set[str] = set()

    for para in doc.paragraphs:
        text = para.text.strip()
        if len(text) < MIN_SEGMENT_CHARS or text in seen_texts:
            continue
        seen_texts.add(text)
        segments.append(RawDocxSegment(text=text))

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if len(text) < MIN_SEGMENT_CHARS or text in seen_texts:
                    continue
                seen_texts.add(text)
                segments.append(RawDocxSegment(text=text))

    return segments
