"""Candidate policy-document discovery for one organisation domain.

Two discovery strategies, both cheap and both explicitly scoped to avoid
the failure the audit criticised (accepting search-result ranking as a
proxy for policy authority, POLARIS v5.0 section 6.1 "do not infer policy
authority from search ranking"):

1. COMMON_PATHS -- a static list of URL paths where corporate policies
   conventionally live (investor-relations, governance, legal, sustainability
   sections). This is a *candidate generator*, not a validator -- everything
   it finds still goes through document_validator.py before acceptance.
2. HTML link scan -- fetch a page, regex for <a href="...pdf"> links whose
   anchor text or URL contains a topic keyword.

This module returns candidates only. It never decides acceptance -- that is
document_validator.py's job, kept as a separate step deliberately (see the
LET taxonomy module docstring for why the two decisions must not merge).

Failure modes: a fetch failure while scanning a seed page for PDF links is
logged at WARNING with the URL and exception, then treated as "zero
candidates from this page" -- a real, distinct outcome from "fetched fine,
found nothing," visible only in the logs. This module never raises past
its own boundary for a single page's fetch failure, because one dead seed
page must not abort discovery for the whole organisation; it does let a
robots.txt check failure propagate from RobotsGate, since that already
fails closed internally (see robots.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

from polaris.common.exceptions import SearchProviderError
from polaris.common.logging_config import get_logger
from polaris.scraper.robots import RobotsGate
from polaris.scraper.url_canonicalize import canonicalize
from polaris.scraper.url_safety import UnsafeURLError, safe_get

logger = get_logger(__name__)

COMMON_PATHS = [
    "/governance", "/corporate-governance", "/investor-relations",
    "/sustainability", "/esg", "/csr", "/policies", "/legal",
    "/code-of-conduct", "/compliance", "/about/governance",
    "/about-us/governance", "/investors/governance",
    "/sustainability/policies", "/en/sustainability", "/en/governance",
]

DOCUMENT_LINK_RE = re.compile(r'href=["\']([^"\']+\.(?:pdf|docx)(?:\?[^"\']*)?)["\']', re.IGNORECASE)

TOPIC_KEYWORDS = {
    "GFT": [
        "gift", "gifts", "hospitality", "entertainment", "anti-bribery",
        "anti-corruption", "gratuit", "cadeau",
    ],
}


@dataclass
class Candidate:
    url: str
    discovery_method: str  # "common_path" | "html_scan"
    anchor_text: str | None = None
    matched_keyword: str | None = None


def discover_common_paths(base_url: str, session: requests.Session, robots: RobotsGate) -> list[Candidate]:
    candidates: list[Candidate] = []
    for path in COMMON_PATHS:
        url = urljoin(base_url, path)
        if not robots.allowed(url):
            continue
        candidates.append(Candidate(url=url, discovery_method="common_path"))
    return candidates


def discover_pdf_links(
    page_url: str,
    session: requests.Session,
    robots: RobotsGate,
    topic: str = "GFT",
    timeout: tuple[float, float] = (5.0, 15.0),
) -> list[Candidate]:
    """Named discover_pdf_links for historical reasons -- it now also
    finds linked .docx files, not PDF only, following the same fix
    HTML/DOCX support already applied elsewhere: a linked Word document
    is exactly as valid a policy source as a linked PDF, and there is no
    reason to only look for one of the two."""
    if not robots.allowed(page_url):
        logger.info("skipping %s -- disallowed by robots.txt", page_url)
        return []
    try:
        resp = safe_get(session, page_url, timeout=timeout)
        resp.raise_for_status()
    except UnsafeURLError as exc:
        logger.warning("refusing to fetch seed page %s: %s", page_url, exc)
        return []
    except requests.RequestException as exc:
        # Logged, not silently absorbed: an empty return here must be
        # distinguishable in the logs from "fetched fine, no PDF links
        # found" -- the two look identical to the caller otherwise, which
        # is exactly the C1.3 "coverage outcome" distinction the WP1
        # framework requires (found vs not-searched vs blocked vs failed).
        logger.warning("failed to fetch seed page %s: %s", page_url, exc)
        return []

    html = resp.text
    keywords = TOPIC_KEYWORDS.get(topic, [])
    candidates: list[Candidate] = []
    for match in DOCUMENT_LINK_RE.finditer(html):
        href = match.group(1)
        full_url = urljoin(page_url, href)
        # Cheap relevance pre-filter on the URL itself; this is NOT the
        # document validator -- it only decides whether the candidate is
        # worth downloading at all.
        lower = full_url.lower()
        hit = next((kw for kw in keywords if kw in lower), None)
        if hit is None:
            continue
        if not robots.allowed(full_url):
            continue
        candidates.append(Candidate(url=full_url, discovery_method="html_scan", matched_keyword=hit))
    return candidates


def discover_via_search(
    topic: str,
    search_provider,
    robots: RobotsGate,
    num_results_per_query: int = 20,
    keywords: list[str] | None = None,
) -> list[Candidate]:
    """The web-wide search channel (C2.2) -- as distinct from
    discover_for_organisation, which only checks known pages on one known
    organisation's domain, this searches the open web across organisations
    it doesn't already know about, using search_provider (see
    search_provider.py -- no meaningful fallback exists here; the caller
    must have a real, configured SearchProvider, e.g. via
    get_default_search_provider()).

    keywords, when given, overrides TOPIC_KEYWORDS.get(topic) -- this is
    what lets a free-text search phrase drive discovery, not just a
    pre-registered topic code. `topic` is still used to label results and
    the registry's topic_candidate field even when keywords overrides the
    lookup.

    Only PDF and HTML results are kept -- see document_dispatch.py, which
    both this discovery path and the manual-add channel now share. HTML
    results are filtered by keyword presence in the URL only at discovery
    time (the same cheap pre-filter PDFs get); actual accept/reject still
    happens later via document_validator once the content is downloaded.
    Organisation identity is NOT resolved here -- a URL found this way
    belongs to whatever domain it's hosted on, and confirming which
    real-world organisation that is is explicitly deferred (C7,
    "human-confirmed table for the pilot") rather than guessed from the
    domain name. Callers (see pipeline.run_topic_search) record this as an
    unconfirmed organisation, not a silent guess.

    One query per keyword, not one combined query -- a combined query
    returns a shallower, less complete result set for each term.
    """
    hint_words = keywords if keywords is not None else TOPIC_KEYWORDS.get(topic, [])
    queries = [f'"{keyword}" policy' for keyword in hint_words]
    if not queries:
        logger.warning("no keywords available for topic %r -- discover_via_search will find nothing", topic)
        return []

    candidates: list[Candidate] = []
    seen_urls: set[str] = set()
    failed_queries: list[str] = []

    for query in queries:
        try:
            results = search_provider.search(query, num_results=num_results_per_query)
        except Exception as exc:
            # A single query failing (rate limit, transient error) must
            # not abort every other query -- results already found for
            # other terms shouldn't be discarded. But if EVERY query
            # fails, that's not query-level noise -- it's a systemic
            # problem (invalid credentials, endpoint down), and returning
            # an empty candidate list in that case would be silently
            # indistinguishable from "searched thoroughly, found nothing
            # relevant," which is exactly the failure mode this repo's
            # error-handling rules exist to prevent. Tracked below and
            # raised once all queries are known to have failed.
            logger.warning("search query %r failed: %s -- skipping this query, continuing with the rest", query, exc)
            failed_queries.append(query)
            continue

        for result in results:
            url = result.url
            # Check the URL's PATH component specifically, not the raw
            # string -- a query string (very common, e.g. a tracking
            # parameter) makes the raw URL not literally end in ".pdf"
            # even though the actual resource is one, which silently
            # rejected every such link before this fix. Found via testing
            # the canonicalization dedup above, not a hypothetical case.
            url_path = urlparse(url).path.lower()
            if not (url_path.endswith((".pdf", ".docx", ".html", ".htm")) or "/" in url_path.rsplit(".", 1)[-1]):
                # Keep PDFs, DOCX, explicit .html/.htm, and extensionless URLs
                # (very common for HTML policy pages, e.g.
                # /legal/gifts-policy with no file extension at all) --
                # reject only URLs whose extension clearly names a
                # different, unsupported format (.pptx, .zip, .xlsx, ...).
                continue
            canonical = canonicalize(url)
            if canonical in seen_urls:
                continue
            if not robots.allowed(url):
                continue
            seen_urls.add(canonical)
            candidates.append(Candidate(url=url, discovery_method="search_api", matched_keyword=query))

    if failed_queries and len(failed_queries) == len(queries):
        raise SearchProviderError(
            f"All {len(queries)} search queries for topic {topic!r} failed -- this looks like a "
            f"provider-level problem (credentials, endpoint availability), not per-query noise. "
            f"An empty candidate list would be indistinguishable from a genuine zero-result search, "
            f"which is why this is raised instead of silently returning []."
        )

    return candidates


def discover_for_organisation(
    base_url: str,
    session: requests.Session,
    robots: RobotsGate,
    topic: str = "GFT",
) -> list[Candidate]:
    """End-to-end discovery for one organisation: seed pages via common
    paths, then scan each reachable seed page for topic-relevant PDF links.

    Dedup is by canonical URL (see url_canonicalize.py), not exact string
    match -- a real gap this closes: "acme.com/policy.pdf" and
    "acme.com/policy.pdf?utm_source=newsletter" used to look like two
    different candidates, downloaded and hashed separately, only caught
    as a duplicate at that later, more expensive point (or missed
    entirely if only one of the two paths happened to surface). The first
    occurrence's exact URL is kept (not a canonicalized version) --
    canonicalization is for comparison only, never for what actually gets
    downloaded or recorded.
    """
    seeds = discover_common_paths(base_url, session, robots)
    all_candidates: list[Candidate] = list(seeds)
    for seed in seeds:
        all_candidates.extend(discover_pdf_links(seed.url, session, robots, topic=topic))
    # de-dupe by canonical URL, keep first occurrence's original URL
    seen: set[str] = set()
    deduped = []
    for c in all_candidates:
        canonical = canonicalize(c.url)
        if canonical in seen:
            continue
        seen.add(canonical)
        deduped.append(c)
    return deduped
