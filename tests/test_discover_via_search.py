import responses

from polaris.scraper.discovery import discover_via_search
from polaris.scraper.robots import RobotsGate
from polaris.scraper.search_provider import SearchResult, StaticSearchProvider


@responses.activate
def test_discover_via_search_keeps_pdf_and_html_rejects_other_file_types():
    responses.add(responses.GET, "https://acme.com/robots.txt", status=404)
    responses.add(responses.GET, "https://other.com/robots.txt", status=404)
    provider = StaticSearchProvider([
        SearchResult(url="https://acme.com/gifts-policy.pdf", title="Gifts Policy", snippet=""),
        SearchResult(url="https://acme.com/legal/gifts-policy", title="Gifts Policy (HTML, no extension)", snippet=""),
        SearchResult(url="https://acme.com/policy-slides.pptx", title="Slides", snippet=""),  # unsupported format
        SearchResult(url="https://other.com/hospitality.pdf", title="Hospitality", snippet=""),
        SearchResult(url="https://other.com/hospitality.html", title="Hospitality (explicit .html)", snippet=""),
    ])
    candidates = discover_via_search("GFT", provider, RobotsGate())
    urls = [c.url for c in candidates]
    assert "https://acme.com/gifts-policy.pdf" in urls
    assert "https://acme.com/legal/gifts-policy" in urls  # extensionless HTML page kept
    assert "https://other.com/hospitality.pdf" in urls
    assert "https://other.com/hospitality.html" in urls
    assert not any(u.endswith(".pptx") for u in urls)  # unsupported format rejected


@responses.activate
def test_discover_via_search_respects_robots_per_domain():
    responses.add(responses.GET, "https://acme.com/robots.txt",
                   body="User-agent: *\nDisallow: /gifts-policy.pdf\n", status=200)
    provider = StaticSearchProvider([
        SearchResult(url="https://acme.com/gifts-policy.pdf", title="Gifts Policy", snippet=""),
    ])
    candidates = discover_via_search("GFT", provider, RobotsGate())
    assert candidates == []


@responses.activate
def test_discover_via_search_dedupes_across_queries():
    """The same URL can plausibly surface from more than one keyword
    query (e.g. "gift" and "hospitality" both matching the same page) --
    must not produce duplicate candidates."""
    responses.add(responses.GET, "https://acme.com/robots.txt", status=404)
    # StaticSearchProvider returns the SAME result set for every query,
    # simulating exactly this overlap.
    provider = StaticSearchProvider([
        SearchResult(url="https://acme.com/gifts-and-hospitality-policy.pdf", title="", snippet=""),
    ])
    candidates = discover_via_search("GFT", provider, RobotsGate())
    urls = [c.url for c in candidates]
    assert urls.count("https://acme.com/gifts-and-hospitality-policy.pdf") == 1


def test_discover_via_search_one_failing_query_does_not_abort_others():
    from polaris.scraper.search_provider import SearchProvider

    class PartiallyFailingProvider(SearchProvider):
        def __init__(self):
            self.calls = []

        def search(self, query, num_results=20):
            self.calls.append(query)
            if "bribery" in query:
                raise Exception("simulated transient failure")
            return [SearchResult(url="https://acme.com/found-it.pdf", title="", snippet="")]

    import responses as responses_lib
    with responses_lib.RequestsMock() as rsps:
        rsps.add(responses_lib.GET, "https://acme.com/robots.txt", status=404)
        provider = PartiallyFailingProvider()
        candidates = discover_via_search("GFT", provider, RobotsGate())

    # At least one query failed, but results from the others must still surface.
    assert len(provider.calls) > 1  # every keyword's query was attempted, not stopped after the first failure
    assert any(c.url == "https://acme.com/found-it.pdf" for c in candidates)


def test_discover_via_search_returns_empty_for_topic_with_no_keywords():
    provider = StaticSearchProvider([SearchResult(url="https://x.com/y.pdf", title="", snippet="")])
    candidates = discover_via_search("UNKNOWN_TOPIC", provider, RobotsGate())
    assert candidates == []


def test_discover_via_search_raises_when_every_query_fails():
    """Regression/design test found during coverage review: if EVERY query
    fails (bad credentials, endpoint down), returning an empty list would
    be silently indistinguishable from a genuine zero-result search. Must
    raise instead."""
    from polaris.scraper.search_provider import SearchProvider
    from polaris.common.exceptions import SearchProviderError
    import pytest

    class AlwaysFailsProvider(SearchProvider):
        def search(self, query, num_results=20):
            raise Exception("simulated: invalid API key")

    with pytest.raises(SearchProviderError, match="All .* search queries"):
        discover_via_search("GFT", AlwaysFailsProvider(), RobotsGate())


@responses.activate
def test_discover_via_search_uses_explicit_keywords_override_not_topic_lookup():
    """This is what lets a free-text search phrase drive discovery,
    instead of requiring a pre-registered topic code."""
    responses.add(responses.GET, "https://acme.com/robots.txt", status=404)
    provider = StaticSearchProvider([
        SearchResult(url="https://acme.com/privacy-policy.pdf", title="Privacy Policy", snippet=""),
    ])
    # "UNKNOWN_TOPIC" has no entry in TOPIC_KEYWORDS at all -- only the
    # explicit keywords override should matter here.
    candidates = discover_via_search(
        "UNKNOWN_TOPIC", provider, RobotsGate(), keywords=["data privacy"],
    )
    assert len(candidates) == 1
    assert candidates[0].matched_keyword == '"data privacy" policy'
