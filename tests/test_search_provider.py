import pytest
import responses

from polaris.common.exceptions import SearchProviderError
from polaris.scraper.search_provider import (
    TavilySearchProvider,
    BraveSearchProvider, SearchResult, StaticSearchProvider, get_default_search_provider,
)


def test_static_provider_returns_configured_results_regardless_of_query():
    results = [SearchResult(url="https://a.com/x.pdf", title="A", snippet="")]
    provider = StaticSearchProvider(results)
    assert provider.search("anything") == results
    assert provider.search("something else entirely") == results


def test_static_provider_respects_num_results_limit():
    results = [SearchResult(url=f"https://a.com/{i}.pdf", title="", snippet="") for i in range(10)]
    provider = StaticSearchProvider(results)
    assert len(provider.search("q", num_results=3)) == 3


def test_get_default_search_provider_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    with pytest.raises(SearchProviderError, match="No search provider is configured"):
        get_default_search_provider()


def test_get_default_search_provider_returns_brave_when_configured(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "fake-key-for-testing")
    provider = get_default_search_provider()
    assert isinstance(provider, BraveSearchProvider)


@responses.activate
def test_brave_provider_parses_real_response_shape(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "fake-key-for-testing")
    responses.add(
        responses.GET, BraveSearchProvider.ENDPOINT, status=200,
        json={"web": {"results": [
            {"url": "https://acme.com/gifts-policy.pdf", "title": "Gifts Policy", "description": "..."},
            {"url": "https://other.com/no-desc.pdf", "title": "No description here"},
        ]}},
    )
    provider = BraveSearchProvider()
    results = provider.search('"gift" policy filetype:pdf')
    assert len(results) == 2
    assert results[0].url == "https://acme.com/gifts-policy.pdf"
    assert results[1].snippet == ""  # missing "description" field handled gracefully


@responses.activate
def test_brave_provider_raises_search_provider_error_on_http_failure(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "fake-key-for-testing")
    responses.add(responses.GET, BraveSearchProvider.ENDPOINT, status=401, json={"error": "unauthorized"})
    provider = BraveSearchProvider()
    with pytest.raises(SearchProviderError):
        provider.search("some query")


@responses.activate
def test_brave_provider_raises_on_malformed_response_shape(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "fake-key-for-testing")
    responses.add(responses.GET, BraveSearchProvider.ENDPOINT, status=200, body="not json at all")
    provider = BraveSearchProvider()
    with pytest.raises(SearchProviderError, match="Could not parse"):
        provider.search("some query")


def test_brave_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    with pytest.raises(KeyError):
        BraveSearchProvider()


def test_get_default_search_provider_returns_tavily_when_configured(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-testing")
    provider = get_default_search_provider()
    assert isinstance(provider, TavilySearchProvider)


def test_get_default_search_provider_prefers_tavily_over_brave_when_both_configured(monkeypatch):
    """Tavily is checked first -- no card, real recurring free tier, zero
    deployment -- so if both keys happen to be set, Tavily wins."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-testing")
    monkeypatch.setenv("BRAVE_API_KEY", "fake-brave-key-for-testing")
    provider = get_default_search_provider()
    assert isinstance(provider, TavilySearchProvider)


@responses.activate
def test_tavily_provider_parses_real_response_shape(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-testing")
    responses.add(
        responses.POST, TavilySearchProvider.ENDPOINT, status=200,
        json={"results": [
            {"url": "https://acme.com/gifts-policy.pdf", "title": "Gifts Policy", "content": "..."},
            {"url": "https://other.com/no-content.pdf", "title": "No content field here"},
        ]},
    )
    provider = TavilySearchProvider()
    results = provider.search('"gift" policy')
    assert len(results) == 2
    assert results[0].url == "https://acme.com/gifts-policy.pdf"
    assert results[1].snippet == ""  # missing "content" field handled gracefully


@responses.activate
def test_tavily_provider_sends_expected_request_shape(monkeypatch):
    """Confirms basic (not advanced) search_depth is used -- 1 credit
    per call, not 2, since POLARIS doesn't need Tavily's own content
    extraction (it has its own PDF/HTML/DOCX pipeline downstream)."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-testing")
    responses.add(responses.POST, TavilySearchProvider.ENDPOINT, status=200, json={"results": []})
    provider = TavilySearchProvider()
    provider.search("test query", num_results=5)

    sent_request = responses.calls[0].request
    assert sent_request.headers["Authorization"] == "Bearer tvly-fake-key-for-testing"
    import json
    body = json.loads(sent_request.body)
    assert body["search_depth"] == "basic"
    assert body["query"] == "test query"
    assert body["max_results"] == 5


@responses.activate
def test_tavily_provider_raises_search_provider_error_on_http_failure(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-testing")
    responses.add(responses.POST, TavilySearchProvider.ENDPOINT, status=401, json={"error": "unauthorized"})
    provider = TavilySearchProvider()
    with pytest.raises(SearchProviderError):
        provider.search("some query")


@responses.activate
def test_tavily_provider_raises_on_malformed_response_shape(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-testing")
    responses.add(responses.POST, TavilySearchProvider.ENDPOINT, status=200, body="not json at all")
    provider = TavilySearchProvider()
    with pytest.raises(SearchProviderError, match="Could not parse"):
        provider.search("some query")


def test_tavily_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(KeyError):
        TavilySearchProvider()


@responses.activate
def test_tavily_provider_caps_num_results_at_20(monkeypatch):
    """Mirrors Brave's own cap -- confirms the actual request sent to
    Tavily is capped at 20 even when a larger num_results is requested,
    not just that a min() exists somewhere in the code."""
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake-key-for-testing")
    responses.add(responses.POST, TavilySearchProvider.ENDPOINT, status=200, json={"results": []})
    provider = TavilySearchProvider()
    provider.search("test query", num_results=50)

    import json
    body = json.loads(responses.calls[0].request.body)
    assert body["max_results"] == 20
