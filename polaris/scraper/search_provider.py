"""The W10 search-discovery channel (C2.2 in the WP1 framework: "search-
provider adapter... provider and query logged per candidate", MUST for
1.0, classified ENG -- known engineering, not R&D).

This is the piece that turns the scraper from "check known pages on one
known organisation's site" into "search the open web for a policy type
across organisations it doesn't already know about" -- the capability
explicitly asked for. Building it does not resolve W10 itself (which
provider is approved, and the budget) -- that stays Mada's call, per the
framework's own decision register. What this module does is make the
provider swappable behind one interface, so W10's actual answer is a
config value, not a rewrite.

As of September 2026, both search APIs the WP1 framework's own EXT section
worried about have gotten worse, not better: Bing Search API was fully
retired (11 Aug 2025, no new signups, ever). Google's Custom Search JSON
API is closed to new customers already, with a hard Jan 2027 cutoff for
existing ones. Brave Search API runs its own independent index rather than
re-routing through Google/Bing, which is a real advantage -- but Brave
also removed its genuinely free tier in February 2026; it now requires a
credit card on file and bills automatically past a small monthly credit,
with no spending cap.

Tavily is the default real provider here instead, specifically because it
has a recurring, no-credit-card-required free tier (1,000 searches/month,
confirmed current as of this session, re-verify before relying on it long
term since this space moves fast) and needs zero infrastructure to stand
up -- sign up, get a key, done. It's a third-party SERP-style API (not an
independent index the way Brave is), which is the real tradeoff for not
needing a card. get_default_search_provider() checks TAVILY_API_KEY first
for that reason, falling back to BRAVE_API_KEY if that's what's
configured instead. Swap either for a different SearchProvider
implementation if a different vendor is chosen for W10; nothing outside
this file needs to change.

Failure modes: unlike llm_classifier.py's RuleBasedFallbackClassifier,
there is no meaningful disposable fallback for search. A fake search
provider returning an empty list is indistinguishable from "searched,
found nothing" -- which would silently look like a clean negative result
instead of "not configured at all." get_default_search_provider()
therefore raises SearchProviderError with a clear message when no API key
is configured, rather than returning something that pretends to work.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

import requests

from polaris.common.exceptions import SearchProviderError
from polaris.common.logging_config import get_logger
from polaris.scraper.http_session import build_session

logger = get_logger(__name__)


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str


class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, num_results: int = 20) -> list[SearchResult]:
        ...


class BraveSearchProvider(SearchProvider):
    """Requires BRAVE_API_KEY. Brave's own independent index (not a
    Google/Bing re-router) is the reason this is the default real
    implementation -- see module docstring. Swap for a different
    SearchProvider if Mada/Claudia pick a different vendor for W10.
    """

    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, session: requests.Session | None = None):
        self._api_key = os.environ["BRAVE_API_KEY"]
        self._session = session or build_session()

    def search(self, query: str, num_results: int = 20) -> list[SearchResult]:
        try:
            resp = self._session.get(
                self.ENDPOINT,
                params={"q": query, "count": min(num_results, 20)},  # Brave caps at 20 per request
                headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
                timeout=(5.0, 15.0),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Brave Search API call failed for query %r: %s", query, exc)
            raise SearchProviderError(f"Brave Search API call failed for query {query!r}: {exc}") from exc

        try:
            payload = resp.json()
            web_results = payload.get("web", {}).get("results", [])
            return [
                SearchResult(url=r["url"], title=r.get("title", ""), snippet=r.get("description", ""))
                for r in web_results if "url" in r
            ]
        except (ValueError, KeyError, TypeError) as exc:
            logger.error("Brave Search API returned an unexpected response shape for query %r: %s", query, exc)
            raise SearchProviderError(f"Could not parse Brave Search API response for query {query!r}: {exc}") from exc


class TavilySearchProvider(SearchProvider):
    """Requires TAVILY_API_KEY. The default real implementation -- see
    module docstring for why: a real, recurring, no-card-required free
    tier and zero deployment, at the cost of not being an independent
    index (Tavily aggregates/re-ranks underlying search results rather
    than running its own crawl the way Brave does).

    search_depth="basic" is used deliberately, not "advanced" -- basic
    costs 1 credit per call versus advanced's 2, and POLARIS doesn't need
    Tavily's content-extraction features (include_raw_content, chunked
    semantic snippets) since document_dispatch.py already has its own
    PDF/HTML/DOCX extraction pipeline downstream. Paying for Tavily to
    pre-extract content POLARIS would just re-extract itself would waste
    the free tier's limited monthly credits for no benefit.
    """

    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, session: requests.Session | None = None):
        self._api_key = os.environ["TAVILY_API_KEY"]
        self._session = session or build_session()

    def search(self, query: str, num_results: int = 20) -> list[SearchResult]:
        try:
            resp = self._session.post(
                self.ENDPOINT,
                json={"query": query, "search_depth": "basic", "max_results": min(num_results, 20)},
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                timeout=(5.0, 15.0),
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Tavily Search API call failed for query %r: %s", query, exc)
            raise SearchProviderError(f"Tavily Search API call failed for query {query!r}: {exc}") from exc

        try:
            payload = resp.json()
            results = payload.get("results", [])
            return [
                SearchResult(url=r["url"], title=r.get("title", ""), snippet=r.get("content", ""))
                for r in results if "url" in r
            ]
        except (ValueError, KeyError, TypeError) as exc:
            logger.error("Tavily Search API returned an unexpected response shape for query %r: %s", query, exc)
            raise SearchProviderError(f"Could not parse Tavily Search API response for query {query!r}: {exc}") from exc


class StaticSearchProvider(SearchProvider):
    """Test/demo fixture: returns pre-configured results regardless of
    query, so discovery logic (query construction, PDF filtering, robots
    checks, dedup) can be tested without a real API key or network call.
    Never used by get_default_search_provider() -- opt-in only, exactly
    like RuleBasedFallbackClassifier's role for the LLM classifier.
    """

    def __init__(self, results: list[SearchResult] | None = None):
        self._results = results or []

    def search(self, query: str, num_results: int = 20) -> list[SearchResult]:
        return self._results[:num_results]


def get_default_search_provider() -> SearchProvider:
    """Raises SearchProviderError -- does not fall back to a fixture --
    when neither TAVILY_API_KEY nor BRAVE_API_KEY is set. See module
    docstring for why search has no meaningful disposable fallback the
    way the LLM classifier does. Tavily is checked first: no credit card,
    a real recurring free tier, zero deployment -- see module docstring
    for the current tradeoffs versus Brave.
    """
    if os.environ.get("TAVILY_API_KEY"):
        return TavilySearchProvider()
    if os.environ.get("BRAVE_API_KEY"):
        return BraveSearchProvider()
    raise SearchProviderError(
        "No search provider is configured (neither TAVILY_API_KEY nor BRAVE_API_KEY is set). "
        "Web-wide topic search cannot run without one -- this is the W10 decision (provider + "
        "budget) still open per the WP1 decision register. Seeded-site discovery "
        "(discover_for_organisation) works without this and does not require a search provider."
    )
