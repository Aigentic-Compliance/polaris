import pytest

from polaris.classifier.html_segmenter import extract_html_preview, segment_html
from polaris.common.exceptions import HTMLParsingError

REAL_POLICY_HTML = """
<html>
<head><title>Global Gifts and Hospitality Policy</title></head>
<body>
<nav><a href="/">Home</a><a href="/legal">Legal</a></nav>
<header>Acme Corp -- Compliance</header>
<script>trackPageView();</script>
<style>.hero { color: red; }</style>
<main>
<h1>Global Gifts and Hospitality Policy</h1>
<p>Employees must not accept any gift exceeding EUR 50 in value.</p>
<p>All gifts must be registered within five business days of receipt.</p>
<ul><li>Cash gifts are never permitted.</li><li>Approval is required above EUR 25.</li></ul>
</main>
<div id="cookie-consent-banner">We use cookies. <button>Accept</button></div>
<footer>&copy; 2026 Acme Corp. All rights reserved.</footer>
</body>
</html>
"""


def test_segment_html_extracts_content_paragraphs_and_list_items():
    segments = segment_html(REAL_POLICY_HTML.encode(), source_id="test-html-1")
    texts = [s.text for s in segments]
    assert "Employees must not accept any gift exceeding EUR 50 in value." in texts
    assert "All gifts must be registered within five business days of receipt." in texts
    assert "Cash gifts are never permitted." in texts
    assert "Approval is required above EUR 25." in texts


def test_segment_html_strips_nav_header_footer_script_style():
    segments = segment_html(REAL_POLICY_HTML.encode(), source_id="test-html-2")
    all_text = " ".join(s.text for s in segments)
    assert "Home" not in all_text
    assert "trackPageView" not in all_text
    assert "color: red" not in all_text
    assert "All rights reserved" not in all_text
    assert "Acme Corp -- Compliance" not in all_text  # header


def test_segment_html_strips_cookie_consent_banner():
    segments = segment_html(REAL_POLICY_HTML.encode(), source_id="test-html-3")
    all_text = " ".join(s.text for s in segments)
    assert "cookies" not in all_text.lower()
    assert "Accept" not in all_text


def test_segment_html_does_not_duplicate_nested_content():
    """A <p> inside an <li> or <td> must produce ONE segment, not two,
    from the outer and inner tag both matching CONTENT_TAGS."""
    html = "<html><body><li><p>Nested content that should appear once.</p></li></body></html>"
    segments = segment_html(html.encode(), source_id="test-html-4")
    matching = [s for s in segments if "Nested content" in s.text]
    assert len(matching) == 1


def test_extract_html_preview_uses_title_tag():
    title, body, page_count = extract_html_preview(REAL_POLICY_HTML.encode())
    assert title == "Global Gifts and Hospitality Policy"
    assert "EUR 50" in body
    assert page_count is None


def test_extract_html_preview_falls_back_to_h1_when_no_title_tag():
    html = "<html><body><h1>Fallback Heading</h1><p>Some content here.</p></body></html>"
    title, body, page_count = extract_html_preview(html.encode())
    assert title == "Fallback Heading"


def test_extract_html_preview_empty_title_and_h1_gives_empty_string():
    html = "<html><body><p>Just content, no title or heading at all.</p></body></html>"
    title, body, page_count = extract_html_preview(html.encode())
    assert title == ""
    assert "Just content" in body


def test_segment_html_short_fragments_filtered():
    html = "<html><body><p>Hi</p><p>A real substantive sentence about gift policy rules.</p></body></html>"
    segments = segment_html(html.encode(), source_id="test-html-5")
    texts = [s.text for s in segments]
    assert "Hi" not in texts  # below MIN_SEGMENT_CHARS-driven meaningful content... actually "Hi" is 2 chars, filtered
    assert any("substantive sentence" in t for t in texts)


def test_html_segments_are_compatible_with_the_classification_cascade():
    """Regression test for a real bug found while wiring HTML support in:
    cascade.classify_segment does `list(raw.bbox)` unconditionally on
    every raw segment -- a bare None bbox would crash with TypeError.
    RawHTMLSegment must carry a real (if meaningless) bbox tuple, not
    None, for structural compatibility with the PDF segment shape the
    cascade was originally written against."""
    from polaris.classifier.cascade import classify_document

    html = b"<html><body><p>Employees must not accept gifts exceeding EUR 50.</p></body></html>"
    segments = segment_html(html, source_id="test-compat")
    result = classify_document(segments, source_id="test-compat")
    assert len(result.segments) == 1
    assert len(result.failures) == 0
    assert result.segments[0].bbox == [0.0, 0.0, 0.0, 0.0]
    assert result.segments[0].page_from is None


def test_segment_html_strips_class_based_boilerplate_hints():
    """BeautifulSoup returns the 'class' attribute as a list of strings,
    not a single string like 'id' -- exercises that branch of
    _strip_chrome specifically."""
    html = '<html><body><div class="newsletter-signup">Subscribe now!</div><p>Real policy content about gifts.</p></body></html>'
    segments = segment_html(html.encode(), source_id="test-html-6")
    all_text = " ".join(s.text for s in segments)
    assert "Subscribe" not in all_text
    assert "Real policy content" in all_text


def test_segment_html_dedupes_identical_text_from_separate_non_nested_tags():
    """Two SEPARATE (non-nested) tags that happen to contain the exact
    same text -- e.g. a sentence repeated in two places on the page --
    must not produce two identical segments."""
    html = "<html><body><p>Standard disclaimer text.</p><li>Standard disclaimer text.</li></body></html>"
    segments = segment_html(html.encode(), source_id="test-html-7")
    matching = [s for s in segments if s.text == "Standard disclaimer text."]
    assert len(matching) == 1
