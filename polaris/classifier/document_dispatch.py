"""Content-type sniffing and dispatch to the right extractor.

This is the actual fix for the gap found in this conversation: pipeline.py
used to call pdf_segmenter.extract_preview unconditionally on every
downloaded candidate, regardless of what was actually downloaded --
discover_common_paths already returns a company's own HTML pages as
candidates, so any policy published as plain HTML (no PDF) got downloaded
successfully and then rejected with a misleading "Could not parse as PDF"
reason. This module is the single place that decides "is this a PDF or
HTML," so that decision is made once, correctly, and by content -- never
by trusting a URL's .pdf extension or a server's possibly-wrong
Content-Type header alone.

Failure modes: raises DocumentParsingError (via extract_preview_any) when
content is neither recognizable PDF nor HTML -- for example, a 404 error
page served as text/plain, or a genuinely corrupt download. This must
propagate as a distinct, informative rejection reason at the pipeline
level, not be silently treated as "empty PDF, no title/body signal,"
which would look identical to a real PDF that legitimately isn't a
policy.
"""

from __future__ import annotations

import io
import zipfile

from polaris.classifier.docx_segmenter import extract_docx_preview, segment_docx
from polaris.classifier.html_segmenter import extract_html_preview, segment_html
from polaris.classifier.pdf_segmenter import extract_preview as extract_pdf_preview
from polaris.classifier.pdf_segmenter import segment_pdf
from polaris.common.exceptions import DocumentParsingError

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


def sniff_document_type(content: bytes) -> str:
    """Returns "pdf", "html", "docx", or "unknown". Sniffs by content, not
    by URL extension or Content-Type header -- both can lie (a server
    misconfigured to serve .pdf URLs as text/html, or vice versa), and
    the actual bytes are the only source of truth that matters for
    whether PyMuPDF, an HTML parser, or python-docx can make sense of them.

    DOCX files are ZIP archives -- the same magic bytes as .xlsx, .pptx,
    or a plain .zip. Distinguishing DOCX specifically means looking inside
    the archive for word/document.xml, not just checking the outer magic
    bytes, or a spreadsheet or slide deck would be misidentified as a
    Word document and fail confusingly deeper in the pipeline instead of
    being cleanly reported as "unknown" here.
    """
    stripped = content.lstrip()
    if stripped[:5] == PDF_MAGIC:
        return "pdf"
    if stripped[:4] == ZIP_MAGIC:
        try:
            with zipfile.ZipFile(io.BytesIO(stripped)) as zf:
                if "word/document.xml" in zf.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
        return "unknown"  # a zip, but not one we recognize (xlsx, pptx, plain zip, corrupt)
    # Cheap, deliberately permissive HTML sniff: real-world HTML in the
    # wild is inconsistent about a leading <!DOCTYPE> or even a leading
    # '<html' tag (some pages start with a comment, a BOM, or whitespace
    # this .lstrip() already handles) -- checking for a case-insensitive
    # '<html' or '<body' somewhere in the first few KB correctly identifies
    # real HTML without needing a full parse just to classify it.
    prefix_lower = stripped[:4096].lower()
    if b"<html" in prefix_lower or b"<!doctype html" in prefix_lower or b"<body" in prefix_lower:
        return "html"
    return "unknown"


def extract_preview_any(content: bytes) -> tuple[str, str, int | None, str]:
    """Returns (title_or_first_page_text, body_sample, page_count, document_type).
    page_count is always None for HTML and DOCX. Raises DocumentParsingError
    (PDFParsingError, HTMLParsingError, DOCXParsingError, or this function's
    own raise for unrecognized content) -- never silently returns empty
    strings, which would be indistinguishable from a real empty/irrelevant
    document to document_validator.py.
    """
    doc_type = sniff_document_type(content)
    if doc_type == "pdf":
        title, body, page_count = extract_pdf_preview(content)
        return title, body, page_count, "pdf"
    if doc_type == "html":
        title, body, page_count = extract_html_preview(content)
        return title, body, page_count, "html"
    if doc_type == "docx":
        title, body, page_count = extract_docx_preview(content)
        return title, body, page_count, "docx"
    raise DocumentParsingError(
        f"Content ({len(content)} bytes) is neither recognizable PDF, HTML, nor DOCX -- "
        f"first 100 bytes: {content[:100]!r}"
    )


def segment_any(content: bytes, source_id: str, document_type: str) -> list:
    """Segments already-classified content (document_type known from a
    prior extract_preview_any call, not re-sniffed) into the format
    appropriate list of raw segments. Callers that need a uniform shape
    convert downstream (see cascade.py's Segment contract, which is
    document-type-agnostic once text and provenance pointers are
    extracted).
    """
    if document_type == "pdf":
        return segment_pdf(content, source_id)
    if document_type == "html":
        return segment_html(content, source_id)
    if document_type == "docx":
        return segment_docx(content, source_id)
    raise ValueError(f"Unknown document_type {document_type!r} -- must be 'pdf', 'html', or 'docx'")
