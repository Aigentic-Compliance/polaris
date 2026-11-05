"""Regression test for a severe bug found in audit: run_for_organisation's
text_extractor and scan_detector parameters defaulted to None, which
silently fell back to empty title/body text -- document_validator.py
always rejects on empty text, so every real PDF the scraper ever
downloaded got rejected, with no error, no warning, nothing to indicate
the pipeline wasn't actually reading document content. This test proves
the DEFAULT call path (no text_extractor passed) now correctly reads a
real PDF's title text, using nothing but responses-mocked HTTP + a real
PDF file, closest to how Mada would actually run this.
"""
import pymupdf as fitz
import responses
from sqlalchemy import create_engine

from polaris.scraper.pipeline import run_for_organisation
from polaris.scraper.source_registry import SourceRegistry


def _make_real_gift_policy_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Global Gifts and Hospitality Policy")
    page.insert_text((72, 120), "Employees must not accept gifts exceeding EUR 50 in value.")
    return doc.tobytes()


@responses.activate
def test_default_call_actually_reads_pdf_content_and_can_accept():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    # /governance is one of the COMMON_PATHS discovery.py tries.
    responses.add(responses.GET, "https://example.com/governance", body=b"<html></html>", status=200,
                   content_type="text/html")

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))

    # Monkeypatch discovery to return exactly one candidate pointing at a
    # real PDF body, so this test is deterministic regardless of whatever
    # HTML link-scanning discovery.py does.
    import polaris.scraper.pipeline as pipeline_module
    from polaris.scraper.discovery import Candidate

    real_pdf_bytes = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://example.com/policy.pdf", body=real_pdf_bytes, status=200)

    original_discover = pipeline_module.discover_for_organisation
    pipeline_module.discover_for_organisation = lambda *a, **kw: [
        Candidate(url="https://example.com/policy.pdf", discovery_method="manual_test")
    ]
    try:
        # Deliberately NOT passing text_extractor or scan_detector --
        # this is the exact call shape that used to silently reject
        # everything.
        result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")
    finally:
        pipeline_module.discover_for_organisation = original_discover

    assert result.downloaded == 1
    # Before the fix: this was always 0 (everything rejected on empty text).
    assert result.accepted == 1
    assert result.rejected == 0

    accepted_docs = registry.all_accepted()
    assert len(accepted_docs) == 1
    assert "gift" in accepted_docs[0]["acceptance_reason"].lower()


def _patch_single_candidate(monkeypatch, url="https://example.com/policy.pdf"):
    import polaris.scraper.pipeline as pipeline_module
    from polaris.scraper.discovery import Candidate

    original = pipeline_module.discover_for_organisation
    monkeypatch.setattr(
        pipeline_module, "discover_for_organisation",
        lambda *a, **kw: [Candidate(url=url, discovery_method="manual_test")],
    )
    return original


@responses.activate
def test_corrupt_pdf_content_recorded_as_distinct_rejection_not_generic_reject(monkeypatch):
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=b"not actually a pdf at all", status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    assert result.downloaded == 1
    assert result.rejected == 1
    assert result.accepted == 0
    docs = registry.stats()
    assert "Could not parse as a document" in list(docs["rejection_reasons"].keys())[0]


@responses.activate
def test_scanned_document_routed_to_needs_review_not_rejected_or_crashed(monkeypatch):
    scanned_pdf_bytes = _make_scanned_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=scanned_pdf_bytes, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    assert result.downloaded == 1
    assert result.needs_review == 1
    assert result.accepted == 0
    assert result.rejected == 0


@responses.activate
def test_real_pdf_with_no_policy_signal_is_rejected(monkeypatch):
    unrelated_pdf = _make_unrelated_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=unrelated_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    assert result.downloaded == 1
    assert result.rejected == 1
    assert result.accepted == 0


def _make_scanned_pdf_bytes() -> bytes:
    import io

    from PIL import Image

    doc = fitz.open()
    page = doc.new_page()
    img = Image.new("RGB", (10, 10), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    page.insert_image(fitz.Rect(0, 0, 500, 700), stream=buf.getvalue())
    return doc.tobytes()


def _make_unrelated_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Employee Vacation and Leave Policy")
    page.insert_text((72, 120), "This document describes how to request annual leave.")
    return doc.tobytes()


@responses.activate
def test_duplicate_hash_is_skipped_not_reprocessed(monkeypatch):
    pdf_bytes = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=pdf_bytes, status=200)
    _patch_single_candidate(monkeypatch)

    from polaris.scraper.hashing import sha256_of_bytes
    from polaris.common.models import SourceDocument
    from datetime import datetime, timezone

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    # Pre-register this exact content's hash, as if a previous run already saw it.
    registry.register(SourceDocument(
        raw_uri="https://example.com/policy.pdf", file_hash=sha256_of_bytes(pdf_bytes),
        retrieved_at=datetime.now(timezone.utc), topic_candidate="GFT", document_type="pdf",
        accepted=True, acceptance_reason="already seen",
    ))

    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")
    assert result.downloaded == 1
    assert result.duplicates_skipped == 1
    assert result.accepted == 0  # not re-processed, not re-counted


def _make_ambiguous_pdf_bytes() -> bytes:
    """Neither a clear title match nor a clear reject. Deliberately spans
    two pages: page 1 (what extract_preview treats as "title text") has
    no topic keyword at all; the topic keyword only appears on page 2,
    inside the broader body sample. This is what actually routes through
    document_validator's ambiguous/needs_review branch -- a single-page
    document with the keyword anywhere on it hits the (more lenient)
    title-match branch instead, since extract_preview's "title" is the
    whole first page's text, not an isolated title line."""
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text((72, 72), "Code of Business Conduct")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Employees must register gifts above the approved threshold.")
    return doc.tobytes()


@responses.activate
def test_ambiguous_document_routed_to_needs_review_via_full_pipeline(monkeypatch):
    """Exercises _process_candidate's needs_review path via
    validate_document's decision, as distinct from the scanned-document
    needs_review path already covered by test_scanned_document_routed_to_needs_review_not_rejected_or_crashed."""
    ambiguous_pdf = _make_ambiguous_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=ambiguous_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    assert result.downloaded == 1
    assert result.needs_review == 1
    assert result.accepted == 0
    assert result.rejected == 0


@responses.activate
def test_classify_accepted_false_by_default_produces_no_classified_documents(monkeypatch):
    """Default behaviour must be unchanged -- no classification, no extra
    cost, regardless of how many documents get accepted."""
    real_pdf = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=real_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    assert result.accepted == 1
    assert result.classified_documents == {}


@responses.activate
def test_classify_accepted_true_populates_classified_documents(monkeypatch):
    """The actual feature: opting in classifies accepted documents and
    returns real Segment results, keyed by source_id."""
    real_pdf = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=real_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT", classify_accepted=True,
    )

    assert result.accepted == 1
    assert len(result.classified_documents) == 1
    accepted_docs = registry.all_accepted()
    source_id = accepted_docs[0]["source_id"]
    assert source_id in result.classified_documents
    classification = result.classified_documents[source_id]
    assert len(classification.segments) > 0
    # The real gift policy fixture text -- confirm segments are genuinely classified.
    assert any(s.label.value in {"THR", "RUL"} for s in classification.segments)


@responses.activate
def test_classify_accepted_does_not_run_on_rejected_documents(monkeypatch):
    """Classification must never spend an API call on a document that was
    rejected -- only accepted documents are worth classifying."""
    unrelated_pdf = _make_unrelated_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=unrelated_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT", classify_accepted=True,
    )

    assert result.rejected == 1
    assert result.classified_documents == {}


@responses.activate
def test_classify_accepted_survives_segmentation_failure_without_losing_the_accept(monkeypatch):
    """If segmentation fails on an accepted document (rare, but a
    different code path than the preview extraction that already
    succeeded), the document must remain accepted and registered -- a
    classification-layer failure must not retroactively un-accept it."""
    real_pdf = _make_real_gift_policy_pdf_bytes()

    from polaris.common.exceptions import PDFParsingError

    def broken_segment_any(*a, **kw):
        raise PDFParsingError("simulated segmentation failure")

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=real_pdf, status=200)
    _patch_single_candidate(monkeypatch)
    monkeypatch.setattr("polaris.scraper.pipeline.segment_any", broken_segment_any, raising=False)
    import polaris.classifier.document_dispatch as dd_module
    monkeypatch.setattr(dd_module, "segment_any", broken_segment_any)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT", classify_accepted=True,
    )

    assert result.accepted == 1  # still accepted despite the classification-layer failure
    assert result.classified_documents == {}  # but nothing to show for it
    assert len(registry.all_accepted()) == 1  # still durably registered


@responses.activate
def test_raw_uri_actually_points_to_stored_bytes_not_the_web_url(monkeypatch, tmp_path):
    """The actual point of raw_store.py: raw_uri must resolve to real,
    readable, byte-identical stored content -- not just be A string, and
    not the placeholder web URL it used to be. This is the fix that makes
    a genuinely separate, later classification step possible at all."""
    real_pdf = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=real_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    raw_dir = tmp_path / "raw_store"
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT", raw_store_dir=str(raw_dir),
    )
    assert result.accepted == 1

    doc = registry.all_accepted()[0]
    assert doc["raw_uri"] != "https://example.com/policy.pdf"  # not the old placeholder behavior
    assert doc["raw_uri"].startswith(str(raw_dir))

    from polaris.scraper.raw_store import load_raw
    stored_bytes = load_raw(doc["raw_uri"], expected_hash=doc["file_hash"])
    assert stored_bytes == real_pdf  # byte-identical to what was actually downloaded and accepted


@responses.activate
def test_raw_store_failure_does_not_block_registration_but_is_visible(monkeypatch, tmp_path):
    """A full disk or permissions problem writing the raw store must not
    silently prevent the document from being registered -- it's a
    distinct, visible infrastructure failure, not a reason to lose the
    accept/reject decision too."""
    real_pdf = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=real_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    from polaris.common.exceptions import RawStoreError

    def broken_save_raw(*a, **kw):
        raise RawStoreError("simulated: disk full")

    monkeypatch.setattr("polaris.scraper.pipeline.save_raw", broken_save_raw)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    assert result.accepted == 1  # still registered despite the storage failure
    doc = registry.all_accepted()[0]
    assert doc["raw_uri"].startswith("STORAGE_FAILED:")  # visibly marked, not silently pretending to have stored it


@responses.activate
def test_llm_qualifier_flows_through_full_pipeline_and_resolves_ambiguous_document(monkeypatch):
    """End-to-end: an ambiguous document (ends up needs_review under pure
    rules) gets a confident second opinion from the LLM qualifier and is
    correctly accepted, with document_class persisted in the registry."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Code of Business Conduct")  # no title match at all
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Employees must register gifts above the approved threshold.")  # body keyword only
    ambiguous_pdf = doc.tobytes()

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=ambiguous_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    from polaris.scraper.llm_qualifier import QualificationResult

    class FakeQualifier:
        def qualify(self, title_text, body_sample, topic, negative_hint=None):
            return QualificationResult(
                document_class="D3", relevance_level="section", unit="section",
                evidence_snippet="Employees must register gifts above the approved threshold.",
                confidence=0.85, rationale="A Code of Conduct section clearly addressing gifts.",
                model_id="fake",
            )

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT", llm_qualifier=FakeQualifier(),
    )

    assert result.accepted == 1
    assert result.needs_review == 0
    accepted_docs = registry.all_accepted()
    assert accepted_docs[0]["document_class"] == "D3"


@responses.activate
def test_without_llm_qualifier_same_ambiguous_document_still_routes_to_review(monkeypatch):
    """Confirms the opt-in nature: the exact same ambiguous document,
    without a qualifier, behaves exactly as it always has -- needs_review,
    no document_class."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Code of Business Conduct")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Employees must register gifts above the approved threshold.")
    ambiguous_pdf = doc.tobytes()

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=ambiguous_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Example Corp", "https://example.com", registry, topic="GFT")

    assert result.needs_review == 1
    assert result.accepted == 0


def test_playwright_fetcher_sees_more_real_content_than_plain_http_end_to_end(monkeypatch):
    """The actual end-to-end proof: the same JS-rendered URL, run through
    the full pipeline twice -- once with the default plain-HTTP fetch,
    once with PlaywrightPageFetcher. The plain-HTTP run sees nothing (the
    script tag's content is stripped as chrome by html_segmenter, since
    it was never executed) and rejects for lack of signal. The Playwright
    run sees the real rendered content and finds a genuine topic keyword
    match, landing in needs_review instead of a flat reject -- a real
    document to review, not silently dropped.
    """
    import http.server
    import threading
    import time

    from polaris.scraper.page_fetcher import PlaywrightPageFetcher

    js_page = b"""<!DOCTYPE html>
<html><head><title>Compliance Hub</title></head>
<body><div id="root">Loading...</div>
<script>
document.getElementById('root').innerHTML =
  '<h1>Company Policy Page</h1><p>Employees must not accept gifts exceeding EUR 50.</p>';
</script></body></html>"""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(js_page)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    url = f"http://127.0.0.1:{port}/policy-page"

    import polaris.scraper.pipeline as pipeline_module
    from polaris.scraper.discovery import Candidate
    monkeypatch.setattr(
        pipeline_module, "discover_for_organisation",
        lambda *a, **kw: [Candidate(url=url, discovery_method="manual_test")],
    )

    try:
        registry_plain = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
        result_plain = run_for_organisation("Example Corp", "https://example.com", registry_plain, topic="GFT")

        registry_rendered = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
        result_rendered = run_for_organisation(
            "Example Corp", "https://example.com", registry_rendered, topic="GFT",
            page_fetcher=PlaywrightPageFetcher(),
        )
    finally:
        server.shutdown()

    # Plain HTTP: script content is stripped as chrome before ever running -- no signal at all.
    assert result_plain.rejected == 1
    plain_reason = registry_plain.stats()["rejection_reasons"]
    assert any("No title or body signal" in reason for reason in plain_reason)

    # Playwright: the real rendered content is seen -- a genuine keyword match,
    # correctly routed to review rather than silently rejected or lost.
    assert result_rendered.needs_review == 1
    assert result_rendered.rejected == 0


def test_render_fallback_heuristic_upgrades_thin_html_to_full_content(monkeypatch):
    """The actual feature: plain HTTP is tried first (cheap, no browser
    launched) for every candidate; only when its extracted content looks
    suspiciously thin does the Playwright fallback fire, and only kept if
    it genuinely found more. This is the same JS-rendered test page as
    test_playwright_fetcher_sees_more_real_content_than_plain_http_end_to_end,
    but run through the FALLBACK path (plain HTTP first, then Playwright
    only as a second attempt) rather than forcing Playwright for every
    candidate from the start.
    """
    import http.server
    import threading
    import time

    from polaris.scraper.page_fetcher import PlaywrightPageFetcher

    js_page = b"""<!DOCTYPE html>
<html><head><title>Compliance Hub</title></head>
<body><div id="root">Loading...</div>
<script>
document.getElementById('root').innerHTML =
  '<h1>Company Policy Page</h1><p>Employees must not accept gifts exceeding EUR 50.</p>';
</script></body></html>"""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(js_page)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    url = f"http://127.0.0.1:{port}/policy-page"

    import polaris.scraper.pipeline as pipeline_module
    from polaris.scraper.discovery import Candidate
    monkeypatch.setattr(
        pipeline_module, "discover_for_organisation",
        lambda *a, **kw: [Candidate(url=url, discovery_method="manual_test")],
    )

    try:
        registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
        result = run_for_organisation(
            "Example Corp", "https://example.com", registry, topic="GFT",
            render_fallback_fetcher=PlaywrightPageFetcher(),  # page_fetcher NOT set -- plain HTTP tried first
        )
    finally:
        server.shutdown()

    # Same outcome as forcing Playwright directly: the real content was
    # found (via the fallback, not by rendering from the start) and
    # routed to review with a genuine keyword match, not silently rejected.
    assert result.needs_review == 1
    assert result.rejected == 0


@responses.activate
def test_render_fallback_not_triggered_when_plain_http_already_has_enough_content(monkeypatch):
    """The heuristic must not fire (and therefore never launch a browser)
    when plain HTTP already returned substantial content -- confirms this
    is a targeted fallback, not "always also try Playwright."""
    real_pdf = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/policy.pdf", body=real_pdf, status=200)
    _patch_single_candidate(monkeypatch)

    class FetcherThatMustNotBeCalled:
        def fetch(self, url):
            raise AssertionError("render_fallback_fetcher must not be called for a PDF with real content")

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT",
        render_fallback_fetcher=FetcherThatMustNotBeCalled(),
    )
    assert result.accepted == 1  # completed normally -- the fake fetcher was never invoked


@responses.activate
def test_render_fallback_fetch_failure_keeps_plain_http_result(monkeypatch):
    """If the render fallback itself fails to fetch (network error, etc.),
    the pipeline must keep the plain-HTTP result rather than aborting the
    candidate entirely."""
    thin_html = b"<html><head><title>Compliance Hub</title></head><body>Loading...</body></html>"
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/thin.html", body=thin_html, status=200)
    _patch_single_candidate(monkeypatch, "https://example.com/thin.html")

    from polaris.common.exceptions import DownloadError

    class AlwaysFailsFetcher:
        def fetch(self, url):
            raise DownloadError("simulated: render fallback network failure")

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT",
        render_fallback_fetcher=AlwaysFailsFetcher(),
    )
    # Falls back to the plain-HTTP (thin) result -- rejected for lack of signal, not crashed.
    assert result.rejected == 1
    assert result.download_failed == 0


@responses.activate
def test_render_fallback_unparseable_content_keeps_plain_http_result(monkeypatch):
    """If the rendered content can't be parsed at all (a genuinely broken
    response from the fallback fetcher), keep the plain-HTTP result."""
    thin_html = b"<html><head><title>Compliance Hub</title></head><body>Loading...</body></html>"
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/thin.html", body=thin_html, status=200)
    _patch_single_candidate(monkeypatch, "https://example.com/thin.html")

    class ReturnsGarbageFetcher:
        def fetch(self, url):
            return b"totally unrecognizable garbage, not html or pdf at all"

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT",
        render_fallback_fetcher=ReturnsGarbageFetcher(),
    )
    assert result.rejected == 1


@responses.activate
def test_render_fallback_rendered_content_already_known_is_duplicate(monkeypatch):
    """If the rendered version happens to be byte-identical to a document
    already in the registry (a real possibility -- the same JS-rendered
    page fetched twice across different runs), it must be caught as a
    duplicate, not re-registered."""
    thin_html = b"<html><head><title>Compliance Hub</title></head><body>Loading...</body></html>"
    rendered_html = b"<html><body><h1>Gifts Policy</h1><p>Employees must not accept gifts over EUR 50 at all under any circumstance whatsoever in this jurisdiction.</p></body></html>"

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/thin.html", body=thin_html, status=200)
    _patch_single_candidate(monkeypatch, "https://example.com/thin.html")

    class FixedRenderFetcher:
        def fetch(self, url):
            return rendered_html

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    # Pre-register the exact rendered content's hash, as if a prior run already saw it.
    from polaris.scraper.hashing import sha256_of_bytes
    from polaris.common.models import SourceDocument
    from datetime import datetime, timezone
    registry.register(SourceDocument(
        raw_uri="/some/prior/path.bin", file_hash=sha256_of_bytes(rendered_html),
        retrieved_at=datetime.now(timezone.utc), topic_candidate="GFT", document_type="html",
        accepted=True, acceptance_reason="already seen",
    ))

    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT",
        render_fallback_fetcher=FixedRenderFetcher(),
    )
    assert result.duplicates_skipped == 1
    assert result.accepted == 0
    assert result.rejected == 0


@responses.activate
def test_render_fallback_storage_failure_is_visible_not_silently_dropped(monkeypatch):
    """The render-fallback path has its own, separate raw-store save call
    from the main path -- confirms its RawStoreError handling is
    independently correct, not just inherited by coincidence."""
    thin_html = b"<html><head><title>Compliance Hub</title></head><body>Loading...</body></html>"
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/thin.html", body=thin_html, status=200)
    _patch_single_candidate(monkeypatch, "https://example.com/thin.html")

    rendered_html = b"<html><body><h1>Gifts Policy</h1><p>Employees must not accept gifts over EUR 50 under any circumstance in this jurisdiction whatsoever.</p></body></html>"

    class FixedRenderFetcher:
        def fetch(self, url):
            return rendered_html

    from polaris.common.exceptions import RawStoreError

    def broken_save_raw(*a, **kw):
        raise RawStoreError("simulated: disk full during render-fallback save")

    monkeypatch.setattr("polaris.scraper.pipeline.save_raw", broken_save_raw)

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation(
        "Example Corp", "https://example.com", registry, topic="GFT",
        render_fallback_fetcher=FixedRenderFetcher(),
    )
    # The document is still registered despite the storage failure -- visibly marked, not lost.
    assert result.accepted + result.needs_review + result.rejected == 1
    all_docs = registry.stats()
    assert all_docs["total_seen"] == 1
