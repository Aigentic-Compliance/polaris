"""The actual regression test for the gap found in this conversation:
discovery.py's discover_common_paths already returns a company's own HTML
pages as candidates, but pipeline.py used to force-feed every candidate
through the PDF-only extractor, so a policy published as plain HTML (no
PDF at all) got downloaded successfully and then rejected with a
misleading "Could not parse as PDF" reason. This proves the fix: a real
HTML gifts policy page is now correctly read and accepted, with
document_type recorded as "html", not "pdf".
"""
import responses
from sqlalchemy import create_engine

from polaris.scraper.pipeline import run_for_organisation
from polaris.scraper.source_registry import SourceRegistry

REAL_HTML_GIFTS_POLICY = """
<html>
<head><title>Gifts and Hospitality Policy</title></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<header>Acme Corp</header>
<main>
<h1>Gifts and Hospitality Policy</h1>
<p>Employees must not accept any gift exceeding EUR 50 in value.</p>
<p>All gifts must be registered within five business days of receipt.</p>
</main>
<footer>&copy; 2026 Acme Corp. All rights reserved.</footer>
</body>
</html>
"""


def _patch_single_candidate(monkeypatch, url):
    import polaris.scraper.pipeline as pipeline_module
    from polaris.scraper.discovery import Candidate

    monkeypatch.setattr(
        pipeline_module, "discover_for_organisation",
        lambda *a, **kw: [Candidate(url=url, discovery_method="common_path")],
    )


@responses.activate
def test_html_only_policy_page_is_correctly_read_and_accepted(monkeypatch):
    responses.add(responses.GET, "https://acme.com/robots.txt", status=404)
    responses.add(
        responses.GET, "https://acme.com/legal/gifts-policy",
        body=REAL_HTML_GIFTS_POLICY, status=200, content_type="text/html",
    )
    _patch_single_candidate(monkeypatch, "https://acme.com/legal/gifts-policy")

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Acme Corp", "https://acme.com", registry, topic="GFT")

    assert result.downloaded == 1
    # Before the fix: this was always "rejected" with "Could not parse as
    # PDF", regardless of the real content -- the actual policy text was
    # never looked at with the right parser.
    assert result.accepted == 1
    assert result.rejected == 0

    docs = registry.all_accepted()
    assert len(docs) == 1
    assert docs[0]["document_type"] == "html"
    assert "gift" in docs[0]["acceptance_reason"].lower()


@responses.activate
def test_html_page_with_no_policy_content_is_correctly_rejected_not_crashed(monkeypatch):
    unrelated_html = "<html><head><title>Contact Us</title></head><body><p>Call us at 555-1234.</p></body></html>"
    responses.add(responses.GET, "https://acme.com/robots.txt", status=404)
    responses.add(responses.GET, "https://acme.com/contact", body=unrelated_html, status=200, content_type="text/html")
    _patch_single_candidate(monkeypatch, "https://acme.com/contact")

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_for_organisation("Acme Corp", "https://acme.com", registry, topic="GFT")

    assert result.downloaded == 1
    assert result.rejected == 1
    assert result.accepted == 0
    docs = registry.stats()
    # A real, non-parsing-related rejection reason -- not "Could not parse", since this HTML parsed fine.
    assert "No title or body signal" in list(docs["rejection_reasons"].keys())[0]
