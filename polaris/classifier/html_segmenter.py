"""HTML -> provenance-preserving segments, the HTML counterpart to
pdf_segmenter.py.

This exists because discovery.py's discover_common_paths already returns
a company's own HTML pages (e.g. https://acme.com/legal) as candidates in
their own right -- not just as jumping-off points for finding linked PDFs.
Before this module existed, pipeline.py force-fed every candidate through
the PDF-only extractor regardless of what was actually downloaded, so a
policy published as plain HTML text (no PDF at all) would be downloaded
successfully and then rejected with a misleading "Could not parse as PDF"
reason -- the actual policy text was sitting right there, just never
looked at. This module, plus document_dispatch.py's content-type sniffing,
is the fix.

Segmentation strategy: extract text from block-level content elements
(<p>, <li>, headings, <blockquote>, <td>) as individual segments -- the
HTML analogue of pdf_segmenter's one-clause-per-paragraph boundary. <nav>,
<header>, <footer>, <script>, <style>, and common cookie-banner/consent
containers are stripped entirely before any text is extracted, rather than
filtered after the fact by content heuristics -- for HTML, the semantic
tags already tell you what's chrome versus content, which PDF text never
does.

Known gap, stated rather than hidden: this fetches raw HTML via a plain
HTTP GET (see scraper/http_session.py) and cannot execute JavaScript. A
policy rendered client-side by a JS framework (React, Vue) with no content
in the initial HTML response will not be found here -- the page will
segment to zero (or near-zero) content, which document_validator.py will
then correctly reject for lack of signal, not silently accept as empty.
This is the C4.4 "rendered snapshot where content is script-built" gap
the WP1 framework marks SHOULD, not MUST -- a real limitation, not an
oversight, and one a headless-browser fetch would resolve if it becomes
common enough to matter for the pilot population.
"""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

from polaris.common.exceptions import HTMLParsingError
from polaris.common.logging_config import get_logger

logger = get_logger(__name__)

# Tags whose content is chrome, not policy text, and must never be
# segmented -- stripped from the tree before any text extraction, not
# filtered afterward by heuristics (semantic tags are a much stronger
# signal here than anything content-based).
STRIP_TAGS = ["nav", "header", "footer", "script", "style", "noscript", "form", "button", "svg"]

# Common cookie-consent / newsletter-signup container identifiers seen in
# the wild. Best-effort, not exhaustive -- these patterns evolve constantly
# and this list is data, not load-bearing logic; a missed one just means
# one extra low-value segment that the keyword pre-filter will likely
# catch anyway (see classifier/keyword_signals.py).
BOILERPLATE_ID_CLASS_HINTS = ["cookie", "consent", "newsletter", "subscribe", "gdpr-banner"]

CONTENT_TAGS = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "td"]

MIN_SEGMENT_CHARS = 3  # matches pdf_segmenter's boilerplate length floor


@dataclass
class RawHTMLSegment:
    """Structurally compatible with pdf_segmenter.RawSegment (text,
    page_from, page_to, bbox) so cascade.classify_segment can consume
    either without needing to know which format it came from -- it reads
    exactly those four attributes and does `list(raw.bbox)` unconditionally,
    which would crash on None, hence the placeholder tuple rather than
    None. page/bbox have no real meaning for HTML (there is no pagination
    or coordinate concept), so they're always None/a zero-tuple; `tag`
    carries the one piece of structural provenance HTML actually has.
    """
    text: str
    tag: str  # which HTML tag this came from -- kept for section_path in the Segment contract later
    page_from: int | None = None
    page_to: int | None = None
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


def _parse(html_bytes: bytes) -> BeautifulSoup:
    try:
        return BeautifulSoup(html_bytes, "html.parser")
    except Exception as exc:  # BeautifulSoup is deliberately lenient; a real raise here is rare but not impossible
        logger.error("failed to parse HTML (%d bytes): %s", len(html_bytes), exc)
        raise HTMLParsingError(f"Could not parse HTML ({len(html_bytes)} bytes): {exc}") from exc


def _strip_chrome(soup: BeautifulSoup) -> None:
    for tag_name in STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for hint in BOILERPLATE_ID_CLASS_HINTS:
        for tag in soup.find_all(attrs={"id": lambda v: v and hint in v.lower()}):
            tag.decompose()
        for tag in soup.find_all(attrs={"class": lambda v: v and any(hint in c.lower() for c in v)}):
            tag.decompose()


def extract_html_preview(html_bytes: bytes, max_chars: int = 4000) -> tuple[str, str, None]:
    """Mirrors pdf_segmenter.extract_preview's signature and role: a cheap
    preview for document_validator.py, not full segmentation. page_count
    is always None -- pagination is not a meaningful concept for HTML, and
    document_validator.py already treats None page_count as "no length
    penalty applies" rather than requiring a real integer.
    """
    soup = _parse(html_bytes)
    title_tag = soup.find("title")
    h1_tag = soup.find("h1")
    title_text = (title_tag.get_text(strip=True) if title_tag else "") or (h1_tag.get_text(strip=True) if h1_tag else "")

    _strip_chrome(soup)
    body_text = soup.get_text(separator=" ", strip=True)
    return title_text[:2000], body_text[:max_chars], None


def segment_html(html_bytes: bytes, source_id: str) -> list[RawHTMLSegment]:
    soup = _parse(html_bytes)
    _strip_chrome(soup)

    segments: list[RawHTMLSegment] = []
    seen_texts: set[str] = set()  # a real (non-boilerplate) sentence duplicated by nested tags (e.g. <li><p>text</p></li>) must not become two segments

    for tag in soup.find_all(CONTENT_TAGS):
        # Skip a tag if one of its ancestors is also in CONTENT_TAGS and
        # was already (or will be) visited -- prevents a <p> inside a <td>
        # or a nested <li><p> from producing the same text twice.
        if tag.find_parent(CONTENT_TAGS) is not None:
            continue
        text = tag.get_text(separator=" ", strip=True)
        if len(text) < MIN_SEGMENT_CHARS:
            continue
        if text in seen_texts:
            continue
        seen_texts.add(text)
        segments.append(RawHTMLSegment(text=text, tag=tag.name))

    return segments
