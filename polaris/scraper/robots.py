"""robots.txt enforcement, with a TTL cache, to RFC 9309 semantics.

This exists specifically because the BUAS audit's finding F1 was that the
student scraper *checked* robots.txt, stored the result, but never actually
skipped disallowed URLs -- the check was cosmetic. RobotsGate.allowed() is
the single choke point every downloader call must pass through; there is no
code path in this repo that downloads a URL without calling it first.

Status-code handling follows WP1 Target Capability & Acceptance Framework
v0.2, C3.1 exactly, which follows RFC 9309: **any 4xx = no restrictions
apply (allow all)**, not just 404; **5xx or a network/unreachable error =
treat as full disallow**. An earlier version of this file only special-cased
404 as allow-all and treated every other non-200 status (including a plain
403) as full disallow -- stricter than the spec, which sounds safer but is
wrong: it would make the crawler refuse URLs on domains where robots.txt
happens to 403 for unrelated reasons (a WAF rule, a misconfigured server),
even though RFC 9309 says a 4xx means the crawler may proceed as if no
robots.txt exists at all. AT-01/AT-02 and invariant I2 in the framework
test exactly this distinction.

Failure modes: every fetch failure (network error, timeout, 5xx) is caught
narrowly (requests.RequestException only -- a bug elsewhere is not silently
absorbed here), logged at WARNING with the URL and reason, and resolved to
the conservative fail-closed disallow-all ruleset. Nothing here ever
returns "allowed" because of an error; ambiguity always resolves to
disallow, and the ambiguity is always visible in the logs, not just in the
return value.
"""

from __future__ import annotations

import time
import urllib.robotparser
from urllib.parse import urlparse

import requests

from polaris.common.logging_config import get_logger
from polaris.scraper.http_session import DEFAULT_USER_AGENT
from polaris.scraper.url_safety import UnsafeURLError, safe_get

logger = get_logger(__name__)


class RobotsGate:
    def __init__(self, session: requests.Session | None = None, ttl_seconds: int = 3600):
        self._session = session or requests.Session()
        self._ttl = ttl_seconds  # framework caps this at <=24h (C3.1); 3600s default is well within that
        self._cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser]] = {}

    def _get_parser(self, base_url: str) -> urllib.robotparser.RobotFileParser:
        now = time.time()
        cached = self._cache.get(base_url)
        if cached and now - cached[0] < self._ttl:
            return cached[1]

        parser = urllib.robotparser.RobotFileParser()
        robots_url = base_url.rstrip("/") + "/robots.txt"

        try:
            resp = safe_get(self._session, robots_url, timeout=(5.0, 10.0))
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
            elif 400 <= resp.status_code < 500:
                # RFC 9309 / C3.1: ANY 4xx means "no restrictions apply", not
                # just 404. A 403 is not evidence of a disallow rule -- it's
                # evidence the crawler couldn't read the file, which the
                # standard treats the same as "there is no file."
                logger.info("robots.txt at %s returned %s -- treating as no restrictions (C3.1)",
                            robots_url, resp.status_code)
                parser.parse([])
            else:
                # 5xx or any other non-4xx failure status: full disallow.
                logger.warning("robots.txt at %s returned %s -- failing closed (full disallow, C3.1)",
                                robots_url, resp.status_code)
                parser.parse(["User-agent: *", "Disallow: /"])
        except UnsafeURLError as exc:
            # Same fail-closed treatment as a network failure below -- an
            # SSRF-blocked target (the original URL, or any redirect hop
            # safe_get() followed and rejected along the way) is exactly
            # as unverifiable as an unreachable one, and this repo already
            # refuses to interact with that host at all for security
            # reasons regardless.
            logger.warning("refusing to fetch robots.txt at %s: %s -- failing closed", robots_url, exc)
            parser.parse(["User-agent: *", "Disallow: /"])
        except requests.RequestException as exc:
            # Network failure / unreachable: full disallow, per C3.1. Logged
            # with the exception so a persistently-unreachable domain is
            # visible in the logs, not indistinguishable from "checked, no
            # restrictions found."
            logger.warning("robots.txt fetch failed for %s (%s) -- failing closed", robots_url, exc)
            parser.parse(["User-agent: *", "Disallow: /"])

        self._cache[base_url] = (now, parser)
        return parser

    def allowed(self, url: str, user_agent: str = DEFAULT_USER_AGENT) -> bool:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        parser = self._get_parser(base_url)
        return parser.can_fetch(user_agent, url)
