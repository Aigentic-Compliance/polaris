"""SSRF (Server-Side Request Forgery) defense.

Every URL this scraper ever fetches ultimately comes from either a scraped
page's own links (discover_pdf_links) or a search API's results
(discover_via_search) -- both are, in principle, attacker-influenceable:
a compromised or malicious target page, or a manipulated search result.
Without this check, nothing stopped a "discovered policy link" from
actually being http://169.254.169.254/latest/meta-data/ (the AWS/GCP/Azure
cloud metadata endpoint every SSRF guide targets first), an internal
service on a private network range, or a non-HTTP scheme like file:// or
gopher:// entirely.

This is checked once, centrally, before every real network fetch in this
package: pipeline.py's _download, both PageFetcher implementations,
discovery.py's page-content fetch, and robots.py's robots.txt fetch all
call is_safe_to_fetch() first. A URL that fails is treated exactly like a
download failure -- logged, counted, never silently skipped without a
trace, and never fetched regardless.

Known residual risk, not fully closed by this: DNS rebinding, where a
hostname resolves to a safe public IP at check time, then a different,
unsafe IP at actual connection time, because resolution and connection
happen as two separate steps in both this check and in requests/Playwright
themselves. Fully closing that needs pinning the resolved IP for the
actual TCP connection too (a custom transport adapter), which is a
defensible future hardening step, not built here -- the current threat
model (scraping public compliance documents from company websites, not
processing arbitrary adversarial input at multi-tenant scale) does not
yet justify that additional complexity, but this should be revisited
before this tool is ever exposed to less-trusted input at scale.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from polaris.common.logging_config import get_logger

logger = get_logger(__name__)

ALLOWED_SCHEMES = {"http", "https"}


def is_safe_to_fetch(url: str) -> tuple[bool, str]:
    """Returns (is_safe, reason). Never raises -- callers decide how to
    handle an unsafe URL (skip it, log it, count it as a failure), same
    as any other pre-fetch validation in this repo.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return False, f"scheme {scheme!r} is not http/https -- refusing to fetch"
    if not parsed.hostname:
        return False, "URL has no hostname"

    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        return False, f"could not resolve hostname {parsed.hostname!r}: {exc}"

    for _family, _type, _proto, _canonname, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False, f"could not parse resolved address {ip_str!r}"
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, (
                f"{parsed.hostname!r} resolves to a private/internal/reserved address "
                f"({ip_str}) -- refusing to fetch"
            )

    return True, ""


MAX_REDIRECTS = 10


def safe_get(session, url: str, **kwargs):
    """A drop-in replacement for session.get(url, **kwargs) that closes
    the redirect-bypass gap plain requests.Session.get() has on its own.

    FOUND AND FIXED HERE, in the pass immediately after is_safe_to_fetch()
    was first added: requests.Session.get() follows HTTP redirects
    automatically by default, and NOTHING re-validated the redirect
    target -- only the original URL ever got checked. This is the single
    most common real-world way SSRF protections get bypassed: a server
    returns a normal-looking 200 for the URL you validated, or a 302 to
    something you never see at all. Proven concretely with two local
    servers before this fix: a "safe-looking" URL 302-redirected to a
    second server and requests followed it with zero re-validation,
    fetching and returning the second server's content transparently.

    Fixed by disabling requests' automatic redirect following
    (allow_redirects=False) and manually following each hop here, with
    is_safe_to_fetch() re-checked on every single redirect target before
    it is followed -- not just the first URL. Raises requests.TooManyRedirects
    if MAX_REDIRECTS is exceeded (a redirect loop or an attacker trying to
    exhaust retries), matching requests' own exception for that case so
    callers' existing requests.RequestException handling still catches it.
    """
    import requests

    current_url = url
    is_safe, reason = is_safe_to_fetch(current_url)
    if not is_safe:
        logger.warning("refusing to fetch %s: %s", current_url, reason)
        raise UnsafeURLError(reason)

    for _ in range(MAX_REDIRECTS):
        resp = session.get(current_url, allow_redirects=False, **kwargs)
        if resp.is_redirect:
            # requests.Response.is_redirect already guarantees a Location
            # header is present (it's defined as "location" in headers AND
            # a redirect status code) -- no separate "what if there's no
            # Location" branch is reachable here, so none is written.
            next_url = resp.headers.get("Location")
            # Location may be relative -- resolve it against the current URL.
            from urllib.parse import urljoin
            next_url = urljoin(current_url, next_url)

            is_safe, reason = is_safe_to_fetch(next_url)
            if not is_safe:
                logger.warning("refusing to follow redirect from %s to %s: %s", current_url, next_url, reason)
                raise UnsafeURLError(f"redirect to {next_url} refused: {reason}")

            logger.info("following validated redirect: %s -> %s", current_url, next_url)
            current_url = next_url
            continue
        return resp

    raise requests.TooManyRedirects(f"Exceeded {MAX_REDIRECTS} redirects starting from {url}")


class UnsafeURLError(Exception):
    """Raised by safe_get() when the original URL or any redirect target
    along the way fails is_safe_to_fetch(). Callers wrap this into their
    own DownloadError, same as any other fetch failure."""
