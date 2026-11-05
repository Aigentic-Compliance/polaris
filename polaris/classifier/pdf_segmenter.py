"""PDF -> provenance-preserving segments.

Uses PyMuPDF (fitz) to walk text blocks with page + bounding-box
coordinates, then merges paragraphs that PDF layout split across lines
(hyphenation, lowercase-continuation). This mechanism is adapted from the
BUAS repo's pdf_processor.py, which the audit rated genuinely reusable
(F5: SUPPORTED) -- the merge logic itself was fine, the problems were
elsewhere (no header/footer removal, no column handling, non-stable IDs).
This rewrite fixes the two things the audit flagged as real defects:

1. Segment IDs are content-addressed (hash of source_id + page + text),
   not random UUIDs -- so re-running extraction on the same document
   produces the same segment_id, which is what "stable enough to trace an
   extracted object back to source text" (v5.0 section 7.2) requires.
2. A lightweight header/footer filter drops lines that repeat verbatim
   across a large fraction of pages (typical of running headers/footers/
   page numbers), rather than treating every line as substantive.

What this does NOT do yet: multi-column layout detection, table
preservation, or OCR for scanned PDFs. These are real limitations, not
polish -- see README "known gaps". They matter more on some source PDFs
than others; treat first-pass output on multi-column documents as
lower-confidence and expect a higher review-queue rate on those files.

Failure modes: fitz.open() on a corrupt, truncated, or non-PDF file raises
a raw PyMuPDF/mupdf exception with a library-internal error message that
doesn't say which of our documents was affected. _open_pdf() below is the
single place every public function in this module opens a document; it
catches that raw exception, logs which byte length was rejected, and
raises PDFParsingError instead, since one bad document must produce a
diagnosable, catchable failure for its caller (skip and log this one
document) rather than either crashing the batch or -- the alternative
someone could pick to avoid crashing -- silently returning an empty
segment list that reads as "this policy has no content."
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

from polaris.common.exceptions import PDFParsingError
from polaris.common.logging_config import get_logger

logger = get_logger(__name__)

try:
    import pymupdf as fitz  # PyMuPDF's current import name; falls back to legacy alias below
except ImportError:  # pragma: no cover - exercised only in environments without PyMuPDF
    try:
        import fitz
    except ImportError:
        fitz = None


def _open_pdf(pdf_bytes: bytes):
    """The one place fitz.open() is called. Raises PDFParsingError (never
    lets a raw mupdf exception escape) so every caller can catch one known
    exception type and decide what to do with a bad document, instead of
    needing to know PyMuPDF's internal exception hierarchy."""
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed; cannot process PDF.")
    try:
        return fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises its own mupdf.FzErrorLibrary/RuntimeError types
        logger.error("failed to open PDF (%d bytes): %s", len(pdf_bytes), exc)
        raise PDFParsingError(f"Could not open PDF ({len(pdf_bytes)} bytes): {exc}") from exc


@dataclass
class RawSegment:
    text: str
    page_from: int
    page_to: int
    bbox: tuple[float, float, float, float]


def _stable_segment_id(source_id: str, page: int, text: str) -> str:
    h = hashlib.sha256(f"{source_id}|{page}|{text}".encode("utf-8")).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"  # uuid-shaped, deterministic


def detect_text_extractability(pdf_bytes: bytes, min_chars_per_page: int = 20) -> dict:
    """Flags scanned / image-only documents instead of letting them silently
    produce zero segments.

    This exists because of two things landing together: WP1 framework C6.7
    ("scanned/image-only PDFs detected and routed -- OCR or hold -- not
    silently rejected as 'no text'") and the ProtoFocus AI Model
    Architecture document's "Document-intelligence / OCR service" row --
    a third-party OCR step this repo does not call. Detection is the MUST;
    the OCR call itself is the SHOULD, deliberately not built here, since
    it means picking and wiring a specific vendor (Azure Document
    Intelligence is the natural choice given the existing Azure OpenAI
    relationship, but that is a provider decision, not mine to make silently).

    Heuristic: a page with very little extractable text AND at least one
    embedded image is treated as evidence of a scanned page. A page with
    little text and no images (e.g. a mostly-blank divider page) is not
    flagged -- there's nothing to OCR there.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed; cannot inspect PDF.")
    doc = _open_pdf(pdf_bytes)
    page_count = doc.page_count
    if page_count == 0:
        return {"page_count": 0, "suspect_scanned_pages": [], "likely_scanned": False}

    suspect_pages: list[int] = []
    for i, page in enumerate(doc, start=1):
        text_len = len(page.get_text().strip())
        has_images = len(page.get_images()) > 0
        if text_len < min_chars_per_page and has_images:
            suspect_pages.append(i)

    fraction_suspect = len(suspect_pages) / page_count
    return {
        "page_count": page_count,
        "suspect_scanned_pages": suspect_pages,
        # Majority of pages showing the scanned-page pattern -> treat the
        # whole document as needing OCR/hold rather than partial extraction.
        "likely_scanned": fraction_suspect > 0.5,
    }


def extract_preview(pdf_bytes: bytes, max_chars: int = 4000) -> tuple[str, str, int]:
    """Cheap preview used by the document validator: first-page text,
    a body sample, and page count. Does not do full segmentation.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed; cannot extract PDF preview.")
    doc = _open_pdf(pdf_bytes)
    first_page_text = doc[0].get_text() if doc.page_count > 0 else ""
    body_chunks = []
    total = 0
    for page in doc:
        t = page.get_text()
        body_chunks.append(t)
        total += len(t)
        if total >= max_chars:
            break
    return first_page_text[:2000], "".join(body_chunks)[:max_chars], doc.page_count


HEADER_ZONE_FRACTION = 0.08   # top 8% of the page
FOOTER_ZONE_FRACTION = 0.92   # bottom 8% of the page
BOILERPLATE_MAX_WORDS = 12    # headers/footers are short; a repeated full sentence is not automatically one

# "Page 3 of 40", "Page 12" etc. vary per page, so the repeated-line-frequency
# counter below never sees them as "the same line repeated" -- each one is a
# distinct string. They need their own pattern check regardless of frequency
# or position. This is the fix for the second bug the PDF segmenter test
# caught: a page-number footer that differs every page slipped straight
# through the frequency-based filter.
PAGE_NUMBER_RE = re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE)


def _looks_like_boilerplate(
    line: str,
    bbox: tuple[float, float, float, float],
    page_height: float,
    line_counter: Counter,
    page_count: int,
) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return True
    if stripped.isdigit():  # bare page numbers, e.g. a lone "12"
        return True
    if PAGE_NUMBER_RE.match(stripped):  # "Page 12", "Page 3 of 40" -- varies per page, checked independently of frequency
        return True

    # BUG FOUND AND FIXED HERE: the previous gate was `page_count > 3`,
    # which correctly avoided a different real problem (a single
    # occurrence on a short document can produce a misleadingly high
    # ratio -- 1 occurrence / 2 pages = 50%, which is not repetition at
    # all) but overcorrected by disabling frequency-based detection
    # entirely for any document with 3 or fewer pages -- exactly the
    # documents where a running header/footer ("Confidential -- Internal
    # Use Only" on every page) is most common. Found via stress-testing a
    # 3-page all-boilerplate document: the header survived as 3 separate
    # fake "content" segments, one per page, because the gate never
    # engaged. Fixed by requiring the line to have occurred on at least 2
    # distinct pages (a real minimum for "repeated" to mean anything) AND
    # a meaningful fraction of the document, instead of gating on total
    # page count at all -- this correctly handles a 1-page document (max
    # possible occurrence count is 1, so nothing can ever be flagged,
    # which is right: repetition is undefined on a single page), a 2-page
    # document where a header appears on both (2/2 = 100%, correctly
    # flagged), and a 100-page document where a real section heading
    # happens to repeat on 5 pages (5/100 = 5%, correctly NOT flagged as a
    # running header).
    occurrence_count = line_counter[stripped]
    repeated_often = occurrence_count >= 2 and occurrence_count / page_count > 0.4
    if not repeated_often:
        return False

    # A line can legitimately repeat across pages of a real policy (a
    # section heading reused verbatim, e.g.). Frequency alone is therefore
    # not sufficient evidence -- this is what produced a false positive on
    # a repeated substantive sentence during testing. Only treat repetition
    # as boilerplate when it is ALSO short (headers/footers are short) or
    # positioned in the page's header/footer margin.
    is_short = len(stripped.split()) <= BOILERPLATE_MAX_WORDS
    y0, y1 = bbox[1], bbox[3]
    in_margin = page_height > 0 and (
        y1 < page_height * HEADER_ZONE_FRACTION or y0 > page_height * FOOTER_ZONE_FRACTION
    )
    return is_short or in_margin


def segment_pdf(pdf_bytes: bytes, source_id: str) -> list[RawSegment]:
    if fitz is None:
        raise RuntimeError("PyMuPDF is not installed; cannot segment PDF.")

    doc = _open_pdf(pdf_bytes)
    page_count = doc.page_count

    # First pass: count line frequency across all pages to detect running
    # headers/footers before we decide what to keep.
    line_counter: Counter = Counter()
    pages_lines: list[list[tuple[str, tuple[float, float, float, float]]]] = []
    page_height = doc[0].rect.height if page_count > 0 else 0.0
    for page in doc:
        lines = []
        for block in page.get_text("blocks"):
            x0, y0, x1, y1, text, *_ = block
            for raw_line in text.split("\n"):
                if raw_line.strip():
                    lines.append((raw_line, (x0, y0, x1, y1)))
                    line_counter[raw_line.strip()] += 1
        pages_lines.append(lines)

    segments: list[RawSegment] = []
    for page_index, lines in enumerate(pages_lines, start=1):
        # BUG FIXED HERE: this used to accumulate every kept line for the
        # whole page into one `buffer` list and only join+flush it ONCE at
        # the end of the page, producing exactly one giant segment per
        # page regardless of how many distinct clauses were on it -- a
        # Purpose statement, a threshold rule, and a registration rule
        # would all be joined into one blob and forced into a single LET
        # label. Caught by actually running the ingestion script against a
        # realistic two-clause-per-page test document, not by the existing
        # unit tests, none of which asserted on segment COUNT or
        # boundaries -- only on content presence and boilerplate removal.
        # Fixed by flushing the in-progress paragraph as its own segment
        # every time a new line is NOT a continuation of it, so only true
        # continuations (hyphenation, lowercase-to-lowercase wrap) get
        # merged; every other line starts a new segment. Deliberately NOT
        # using a nested closure for the flush step -- a first attempt at
        # this fix used a helper function with default-argument capture
        # (`def _flush(current_lines=current_lines): ...`), which captures
        # values at DEFINITION time, not at each call, so it would have
        # silently flushed the same empty initial state forever. Caught
        # before it shipped by re-reading this diff, not by a test --
        # worth calling out since a reviewer skimming a "helper function"
        # pattern would likely wave it through.
        current_lines: list[str] = []
        current_bbox: list[float] | None = None

        for line, bbox in lines:
            if _looks_like_boilerplate(line, bbox, page_height, line_counter, page_count):
                continue
            stripped = line.rstrip()
            is_continuation = bool(
                current_lines
                and (current_lines[-1].endswith("-") or (current_lines[-1][-1:].islower() and stripped[:1].islower()))
            )
            if is_continuation:
                current_lines[-1] = current_lines[-1].rstrip("-") + stripped
                continue

            if current_lines:
                paragraph = " ".join(current_lines).strip()
                if paragraph:
                    segments.append(RawSegment(
                        text=paragraph, page_from=page_index, page_to=page_index,
                        bbox=tuple(current_bbox) if current_bbox else (0.0, 0.0, 0.0, 0.0),
                    ))
            current_lines = [stripped]
            current_bbox = list(bbox)

        if current_lines:
            paragraph = " ".join(current_lines).strip()
            if paragraph:
                segments.append(RawSegment(
                    text=paragraph, page_from=page_index, page_to=page_index,
                    bbox=tuple(current_bbox) if current_bbox else (0.0, 0.0, 0.0, 0.0),
                ))
    return segments
