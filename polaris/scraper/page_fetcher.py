"""Pluggable page fetching -- plain HTTP by default, a real rendered
browser (Playwright) when JavaScript-rendered content needs it.

Same abstraction shape as search_provider.py: one interface, a real
default implementation, and no silent fallback that pretends a page was
fetched when it wasn't. RobotsGate.allowed() is checked at discovery time
regardless of which fetcher is used -- this module has no opinion about
robots.txt and must never be handed a URL that hasn't already cleared that
check, exactly like the plain-requests download path it sits alongside.

Why this exists: discover_common_paths/discover_via_search can return a
URL whose actual policy content only appears after client-side JavaScript
runs. A plain HTTP GET (RequestsPageFetcher, the default, used everywhere
until now) sees only the pre-render HTML shell -- for a JS-heavy page,
that's often empty or near-empty, which segments to nothing and gets
correctly rejected for lack of signal, but the real content was never
looked at with the right tool. PlaywrightPageFetcher fixes this by
actually running the page's JavaScript before reading its content.

Deliberately NOT using a hosted service (Browserbase, Browserless) --
Playwright drives a real browser directly, no vendor, no API key, no
per-request cost, matching this project's "boring, no new vendor unless
justified" default. Stealth/anti-bot-detection features some hosted
services bundle are deliberately not used here -- C3.4 in the WP1
framework is explicit: "No access behind login, paywall, CAPTCHA or
technical protection; never circumvent." This fetcher renders pages a
real browser would render normally; it does not attempt to defeat bot
detection, and should never be extended to do so.

Failure modes: PlaywrightPageFetcher raises DownloadError (the same
exception plain HTTP downloads raise) on a navigation timeout or any
Playwright-internal error -- callers already handle DownloadError as
"this candidate could not be fetched," so no new failure-handling path is
needed anywhere that uses this. A browser instance is launched and closed
per call rather than kept alive across calls -- simpler, and correct for
this repo's current call volume; pooling would be the right upgrade if
volume ever made per-call launch overhead a real cost, not before.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import requests

from polaris.common.exceptions import DownloadError
from polaris.common.logging_config import get_logger
from polaris.scraper.url_safety import UnsafeURLError, is_safe_to_fetch, safe_get

logger = get_logger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # same cap as pipeline.py's plain-HTTP path


class PageFetcher(ABC):
    @abstractmethod
    def fetch(self, url: str) -> bytes:
        ...


class RequestsPageFetcher(PageFetcher):
    """Plain HTTP GET -- the existing default behaviour, wrapped in this
    interface so it's swappable with PlaywrightPageFetcher via the same
    parameter. Does not execute JavaScript."""

    def __init__(self, session: requests.Session, timeout: tuple[float, float] = (5.0, 20.0)):
        self._session = session
        self._timeout = timeout

    def fetch(self, url: str) -> bytes:
        try:
            resp = safe_get(self._session, url, timeout=self._timeout, stream=True)
            resp.raise_for_status()
        except UnsafeURLError as exc:
            logger.warning("refusing to fetch %s: %s", url, exc)
            raise DownloadError(f"Refused to fetch {url}: {exc}") from exc
        except requests.RequestException as exc:
            raise DownloadError(f"GET {url} failed: {exc}") from exc

        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise DownloadError(f"GET {url} exceeded {MAX_DOWNLOAD_BYTES} byte cap -- aborted mid-stream")
            chunks.append(chunk)
        return b"".join(chunks)


class PlaywrightPageFetcher(PageFetcher):
    """Fetches a URL by actually rendering it in headless Chromium and
    reading the DOM after JavaScript has run -- for pages a plain GET
    sees as empty. Requires `pip install playwright` and
    `playwright install chromium` (browser binaries are a separate
    download from the Python package).

    SSRF protection here does NOT use page.route() -- that was the first
    attempt, and it turned out not to work: tested directly against a real
    redirect (two local servers, one 302-ing to the other), route()'s
    handler fired exactly once, for the initial URL, and never again for
    the redirect target. Chromium follows a navigation-level 3xx redirect
    internally in a way page.route() does not re-intercept, even though
    page.on("request") does see every hop (confirmed with the same test).
    Trusting route() without testing it against a real redirect would have
    shipped a fix that looked complete and wasn't.

    The actual working mechanism: page.on("request") fires for every
    request Chromium makes, including each redirect hop and any in-page
    JavaScript fetch/XHR call. The handler checks each one against
    is_safe_to_fetch() and, on a match, closes the page immediately --
    confirmed directly (including a BrokenPipeError on the target
    server's side) that this genuinely aborts the in-flight connection
    before its response is read, not just that the result gets discarded
    afterward. page.goto() then raises (net::ERR_ABORTED), which is
    caught below and converted to the same DownloadError every other
    failure in this class produces.
    """

    def __init__(self, wait_until: str = "networkidle", timeout_ms: int = 20000):
        self._wait_until = wait_until
        self._timeout_ms = timeout_ms

    def fetch(self, url: str) -> bytes:
        is_safe, reason = is_safe_to_fetch(url)
        if not is_safe:
            logger.warning("refusing to render %s: %s", url, reason)
            raise DownloadError(f"Refused to fetch {url}: {reason}")

        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        blocked_url = None

        def _validate_request(request):
            nonlocal blocked_url
            safe, block_reason = is_safe_to_fetch(request.url)
            if not safe:
                blocked_url = request.url
                logger.warning("blocking in-flight request to %s during render of %s: %s",
                                request.url, url, block_reason)
                try:
                    page.close()
                except PlaywrightError:
                    pass  # already closing -- fine, the point was to abort the in-flight request

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page()
                    page.on("request", _validate_request)
                    page.goto(url, wait_until=self._wait_until, timeout=self._timeout_ms)
                    html = page.content()
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            if blocked_url:
                raise DownloadError(f"Refused to fetch (unsafe request to {blocked_url} during render of {url})") from exc
            logger.warning("Playwright navigation timed out for %s: %s", url, exc)
            raise DownloadError(f"Rendering {url} timed out: {exc}") from exc
        except PlaywrightError as exc:
            if blocked_url:
                raise DownloadError(f"Refused to fetch (unsafe request to {blocked_url} during render of {url})") from exc
            logger.warning("Playwright failed to render %s: %s", url, exc)
            raise DownloadError(f"Could not render {url}: {exc}") from exc

        content = html.encode("utf-8")
        if len(content) > MAX_DOWNLOAD_BYTES:
            raise DownloadError(f"Rendered content for {url} exceeded {MAX_DOWNLOAD_BYTES} byte cap")
        return content
