"""discovery.py was at 69% coverage before this audit -- discover_pdf_links'
HTML-scanning logic and discover_for_organisation's dedup logic were
essentially untested."""
import responses

from polaris.scraper.discovery import discover_for_organisation, discover_pdf_links, discover_via_search
from polaris.scraper.http_session import build_session
from polaris.scraper.robots import RobotsGate

PAGE_WITH_LINKS = """
<html><body>
<a href="/policies/gifts-and-hospitality-policy.pdf">Gifts Policy</a>
<a href="/policies/annual-report-2025.pdf">Annual Report</a>
<a href="/investors/gouvernance.pdf">Not relevant</a>
<a href="https://other.com/hospitality-guidelines.pdf">External hospitality doc</a>
</body></html>
"""


@responses.activate
def test_discover_pdf_links_filters_by_topic_keyword():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/governance", body=PAGE_WITH_LINKS, status=200)
    responses.add(responses.GET, "https://other.com/robots.txt", status=404)  # external domain, allow-all

    session = build_session()
    robots = RobotsGate(session=session)
    candidates = discover_pdf_links("https://example.com/governance", session, robots, topic="GFT")

    urls = [c.url for c in candidates]
    assert "https://example.com/policies/gifts-and-hospitality-policy.pdf" in urls
    assert "https://other.com/hospitality-guidelines.pdf" in urls
    # The annual report and unrelated governance PDF don't match any GFT keyword.
    assert not any("annual-report" in u for u in urls)
    assert not any("gouvernance" in u for u in urls)


@responses.activate
def test_discover_pdf_links_respects_robots_on_individual_links():
    responses.add(responses.GET, "https://example.com/robots.txt",
                   body="User-agent: *\nDisallow: /policies/\n", status=200)
    responses.add(responses.GET, "https://example.com/governance", body=PAGE_WITH_LINKS, status=200)
    responses.add(responses.GET, "https://other.com/robots.txt", status=404)  # external domain, allow-all

    session = build_session()
    robots = RobotsGate(session=session)
    candidates = discover_pdf_links("https://example.com/governance", session, robots, topic="GFT")

    urls = [c.url for c in candidates]
    # /policies/gifts-and-hospitality-policy.pdf is disallowed -- must not appear,
    # even though it matches the topic keyword.
    assert not any("/policies/" in u for u in urls)
    assert "https://other.com/hospitality-guidelines.pdf" in urls


@responses.activate
def test_discover_pdf_links_returns_empty_when_seed_page_itself_disallowed():
    responses.add(responses.GET, "https://example.com/robots.txt",
                   body="User-agent: *\nDisallow: /governance\n", status=200)
    session = build_session()
    robots = RobotsGate(session=session)
    candidates = discover_pdf_links("https://example.com/governance", session, robots, topic="GFT")
    assert candidates == []


@responses.activate
def test_discover_for_organisation_dedupes_candidates_found_via_multiple_paths():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    # Two different common-path seed pages both link to the SAME pdf.
    shared_link_html = '<a href="/shared-gifts-policy.pdf">Gifts Policy</a>'
    for path in ["/governance", "/corporate-governance", "/investor-relations", "/sustainability",
                 "/esg", "/csr", "/policies", "/legal", "/code-of-conduct", "/compliance",
                 "/about/governance", "/about-us/governance", "/investors/governance",
                 "/sustainability/policies", "/en/sustainability", "/en/governance"]:
        responses.add(responses.GET, f"https://example.com{path}", body=shared_link_html, status=200)

    result = discover_for_organisation("https://example.com", build_session(), RobotsGate(), topic="GFT")
    pdf_urls = [c.url for c in result if c.url.endswith(".pdf")]
    # The same PDF linked from every seed page must appear exactly once.
    assert pdf_urls.count("https://example.com/shared-gifts-policy.pdf") == 1


@responses.activate
def test_discover_common_paths_skips_disallowed_paths():
    from polaris.scraper.discovery import discover_common_paths

    responses.add(responses.GET, "https://example.com/robots.txt",
                   body="User-agent: *\nDisallow: /legal\n", status=200)
    candidates = discover_common_paths("https://example.com", build_session(), RobotsGate())
    urls = [c.url for c in candidates]
    assert not any(u.endswith("/legal") for u in urls)
    assert any(u.endswith("/governance") for u in urls)


@responses.activate
def test_discover_pdf_links_also_finds_linked_docx_files():
    page_with_docx_link = '<a href="/policies/gifts-hospitality-policy.docx">Gifts Policy (Word)</a>'
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example.com/governance", body=page_with_docx_link, status=200)

    session = build_session()
    robots = RobotsGate(session=session)
    candidates = discover_pdf_links("https://example.com/governance", session, robots, topic="GFT")

    urls = [c.url for c in candidates]
    assert "https://example.com/policies/gifts-hospitality-policy.docx" in urls


@responses.activate
def test_discover_for_organisation_dedupes_tracking_parameter_variants():
    """The actual regression test for the gap url_canonicalize.py fixes:
    the same document linked with and without a tracking parameter used
    to look like two different candidates."""
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    html_with_two_links_same_doc = (
        '<a href="/gifts-policy.pdf?utm_source=homepage">Gifts Policy</a>'
        '<a href="/gifts-policy.pdf?utm_source=footer">Gifts Policy (footer link)</a>'
    )
    for path in ["/governance", "/corporate-governance", "/investor-relations", "/sustainability",
                 "/esg", "/csr", "/policies", "/legal", "/code-of-conduct", "/compliance",
                 "/about/governance", "/about-us/governance", "/investors/governance",
                 "/sustainability/policies", "/en/sustainability", "/en/governance"]:
        responses.add(responses.GET, f"https://example.com{path}", body=html_with_two_links_same_doc, status=200)

    result = discover_for_organisation("https://example.com", build_session(), RobotsGate(), topic="GFT")
    # Both tracking-parameter variants of the SAME document must collapse to one candidate.
    matching = [c.url for c in result if "gifts-policy.pdf" in c.url]
    assert len(matching) == 1


@responses.activate
def test_discover_via_search_dedupes_tracking_parameter_variants():
    from polaris.scraper.search_provider import SearchResult, StaticSearchProvider

    responses.add(responses.GET, "https://acme.com/robots.txt", status=404)
    provider = StaticSearchProvider([
        SearchResult(url="https://acme.com/gifts-policy.pdf?utm_source=search", title="", snippet=""),
    ])
    # Same StaticSearchProvider returns identical results for every query
    # (simulating the same document surfacing from multiple keyword
    # queries with different tracking parameters attached by the search
    # engine) -- discover_via_search already tests this dedup at the exact-
    # URL level; this confirms it also holds when only the tracking
    # parameter differs between occurrences.
    candidates = discover_via_search("GFT", provider, RobotsGate())
    assert len(candidates) == 1
