"""URL canonicalization -- C5.1 in the WP1 framework, never built until
now. Before this existed, discovery.py's dedup logic (both
discover_for_organisation and discover_via_search) compared URLs by exact
string equality, so the same document reachable via
"acme.com/policy.pdf" and "acme.com/policy.pdf?utm_source=newsletter"
looked like two different candidates -- downloaded, hashed, and only
caught as a duplicate at that later, more expensive point (or not at all,
if the tracking parameter happened to differ between two genuinely
separate discovery paths that both point at the same resource).

Built on w3lib.url.canonicalize_url (from Scrapy's own URL utilities,
BSD-licensed, mature) for the well-established transformations it already
gets right -- percent-encoding normalization, host lowercasing, query
parameter sorting, fragment removal. What w3lib deliberately does NOT do,
because it's application-specific and not something a general library can
safely assume, is decide which query parameters are tracking noise versus
meaningful. That part is this module's own job: a maintained list of
well-known tracking parameters, stripped before handing the URL to w3lib.

Deliberately NOT stripping trailing slashes or normalizing path segments
beyond what w3lib already does -- some servers genuinely serve different
content at "/policy" versus "/policy/", and guessing they're the same
resource risks silently merging two different documents. When in doubt,
under-canonicalize rather than over-canonicalize: a missed duplicate costs
one redundant download; a wrongly-merged pair of distinct documents costs
a real document silently disappearing from the results.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from w3lib.url import canonicalize_url

# Well-known tracking parameters, not an exhaustive list -- new ones can
# be added here as they're noticed. A missed one just means an occasional
# near-duplicate isn't caught before download, not a wrong result (the
# existing byte-hash dedup in source_registry.py still catches an exact
# re-download later).
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
    "mc_cid", "mc_eid",
    "ref", "referrer", "source",
    "_hsenc", "_hsmi", "hsCtaTracking",
    "igshid", "s_cid", "vero_id",
}


def canonicalize(url: str) -> str:
    """Returns a canonical form of url suitable for dedup comparison.
    Two URLs that are the same document reached different ways --
    different tracking parameters, different case in the host, a stray
    fragment -- canonicalize to the same string; two URLs that are
    genuinely different resources do not.
    """
    parsed = urlparse(url)
    kept_params = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in TRACKING_PARAMS]
    stripped = urlunparse(parsed._replace(query=urlencode(kept_params)))
    return canonicalize_url(stripped)
