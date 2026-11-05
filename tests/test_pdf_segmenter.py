import pymupdf as fitz

from polaris.classifier.pdf_segmenter import detect_text_extractability, extract_preview, segment_pdf


def _make_pdf_bytes() -> bytes:
    """4 pages. Header/footer repeat verbatim on every page (real
    boilerplate). Body content differs per page (a real policy's clauses
    are not identical page to page) so the test can distinguish
    'repeated because it's a header' from 'repeated because the test
    author copy-pasted the same sentence on every page' -- the latter is
    what the first version of this test accidentally did, and it exposed
    a real gap in the frequency-only boilerplate heuristic (fixed in
    pdf_segmenter._looks_like_boilerplate: repetition is only boilerplate
    evidence when combined with short length or header/footer position).
    """
    doc = fitz.open()
    for i in range(4):
        page = doc.new_page()
        page.insert_text((72, 40), "Gifts and Hospitality Policy")  # header, in margin, repeats -> boilerplate
        page.insert_text((72, 120), f"Clause {i + 1}: employees must not accept gifts exceeding EUR {50 + i} in value.")
        page.insert_text((72, 780), f"Page {i + 1} of 4")  # footer, in margin -> boilerplate
    return doc.tobytes()


def test_extract_preview_returns_title_body_and_page_count():
    pdf_bytes = _make_pdf_bytes()
    title, body, page_count = extract_preview(pdf_bytes)
    assert page_count == 4
    assert "Gifts and Hospitality Policy" in title
    assert "EUR 50" in body


def test_segment_pdf_drops_header_and_footer_but_keeps_body():
    pdf_bytes = _make_pdf_bytes()
    segments = segment_pdf(pdf_bytes, source_id="test-source")

    all_text = " ".join(s.text for s in segments)
    # All four distinct clauses (varying per page) must survive.
    for i in range(4):
        assert f"EUR {50 + i}" in all_text
    # The repeated header/footer, sitting in the page margins, must not.
    assert "Gifts and Hospitality Policy" not in all_text
    assert "Page 1 of 4" not in all_text
    assert all(s.page_from == s.page_to for s in segments)


def test_segment_pdf_keeps_a_repeated_but_non_margin_substantive_line():
    """A section heading like 'Section 3: Approvals' might legitimately
    repeat across pages if the document restates it, but it is neither
    short-and-margin-positioned like a footer nor a full sentence that
    should be filtered. This guards against re-introducing a purely
    frequency-based filter that would drop legitimate repeated content."""
    doc = fitz.open()
    for i in range(5):
        page = doc.new_page()
        page.insert_text((72, 300), "This clause on gifts must not be construed to permit any exception to the general threshold rule.")
    pdf_bytes = doc.tobytes()
    segments = segment_pdf(pdf_bytes, source_id="test-source-2")
    all_text = " ".join(s.text for s in segments)
    assert "general threshold rule" in all_text


def test_detects_likely_scanned_document():
    """A page with an embedded image and no extractable text is the
    signature of a scanned page. See WP1 framework C6.7 -- this must be
    flagged, not silently treated as 'zero segments found'."""
    import io

    from PIL import Image

    doc = fitz.open()
    page = doc.new_page()
    # A real, validly-encoded 2x2 PNG with no text layer -- simulates a
    # scanned page image with nothing extractable. (An earlier version of
    # this fixture hand-wrote PNG bytes that turned out to have a corrupt
    # zlib stream -- PyMuPDF rejected it with a library error unrelated to
    # the code under test. Generating it with PIL instead of by hand fixes
    # the fixture; detect_text_extractability itself was never the problem.)
    img = Image.new("RGB", (2, 2), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(0, 0, 100, 100), stream=buf.getvalue())
    pdf_bytes = doc.tobytes()

    result = detect_text_extractability(pdf_bytes)
    assert result["likely_scanned"] is True
    assert result["suspect_scanned_pages"] == [1]


def test_normal_text_document_not_flagged_as_scanned():
    pdf_bytes = _make_pdf_bytes()  # from earlier in this file -- real text, no images
    result = detect_text_extractability(pdf_bytes)
    assert result["likely_scanned"] is False
    assert result["suspect_scanned_pages"] == []


def test_corrupt_pdf_raises_pdf_parsing_error_not_raw_mupdf_exception():
    from polaris.classifier.pdf_segmenter import segment_pdf
    from polaris.common.exceptions import PDFParsingError

    garbage = b"this is not a pdf file at all, just some bytes %^&*()"
    try:
        segment_pdf(garbage, source_id="test-source")
        assert False, "expected PDFParsingError"
    except PDFParsingError as exc:
        assert "Could not open PDF" in str(exc)


def test_corrupt_pdf_raises_pdf_parsing_error_for_preview_too():
    from polaris.common.exceptions import PDFParsingError

    garbage = b"also not a pdf"
    try:
        extract_preview(garbage)
        assert False, "expected PDFParsingError"
    except PDFParsingError:
        pass


def test_multiple_distinct_clauses_on_one_page_become_separate_segments():
    """Regression test for a real bug found while testing the local
    ingestion script against a realistic document: segment_pdf used to
    accumulate every kept line on a page into one buffer and flush it as
    a SINGLE segment at the end of the page -- a Purpose statement, a
    threshold rule, and a registration rule on the same page all got
    merged into one blob, forcing them into a single LET label. Fixed by
    flushing each non-continuation line as its own segment."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Purpose: this policy sets out the rules for offering gifts.")
    page.insert_text((72, 100), "Employees must not accept any gift exceeding EUR 50 in value.")
    page.insert_text((72, 130), "All gifts must be registered within five business days of receipt.")
    pdf_bytes = doc.tobytes()

    segments = segment_pdf(pdf_bytes, source_id="test-source-3")

    assert len(segments) == 3
    assert "Purpose" in segments[0].text
    assert "EUR 50" in segments[1].text
    assert "registered" in segments[2].text
    # None of the three should have bled into another.
    assert "EUR 50" not in segments[0].text
    assert "registered" not in segments[1].text


def test_short_document_header_footer_still_filtered():
    """Regression test for a real bug found by stress-testing: the
    repeated-line detector used to require page_count > 3 before it would
    engage at all, so a 3-page (or fewer) document's running header/footer
    survived as one fake 'content' segment per page. Fixed by requiring
    the line to occur on at least 2 distinct pages instead of gating on
    total page count."""
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page()
        page.insert_text((72, 40), "Confidential -- Internal Use Only")
        page.insert_text((72, 100), f"Unique substantive clause number {i + 1} for this page.")
    pdf_bytes = doc.tobytes()

    segments = segment_pdf(pdf_bytes, source_id="test-source-4")
    assert len(segments) == 3  # one real clause per page, header filtered on every page
    assert all("Confidential" not in s.text for s in segments)


def test_unique_sentence_on_short_document_not_falsely_flagged_as_boilerplate():
    """The inverse risk the old page_count>3 gate was (over-)defending
    against: a line that occurs only ONCE must never be treated as
    boilerplate just because a small page count makes the naive ratio
    look high (1 occurrence / 2 pages = 50%). Fixed by requiring at least
    2 occurrences before the ratio is even computed."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "This unique clause appears only once in the whole document.")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "This is a completely different unique clause on the second page.")
    pdf_bytes = doc.tobytes()

    segments = segment_pdf(pdf_bytes, source_id="test-source-5")
    assert len(segments) == 2
