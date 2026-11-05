"""Shared test configuration.

is_safe_to_fetch() (url_safety.py) does real DNS resolution -- correct and
necessary for the actual SSRF protection it provides, but incompatible
with the synthetic domains (example.com, acme.com, etc.) every other test
in this suite uses via responses-mocked HTTP, none of which resolve via
real DNS. This autouse fixture patches the NAME as imported into each
calling module (pipeline.py, page_fetcher.py, discovery.py, robots.py) to
always return "safe" by default, so the existing test suite doesn't need
every fake domain to be a real, resolvable one.

This does NOT patch polaris.scraper.url_safety.is_safe_to_fetch itself --
only each caller's own local binding of it -- so test_url_safety.py's own
tests (which import and call the real function directly) are completely
unaffected by this fixture and still exercise the real DNS-based logic.
Any test that wants to verify the real SSRF wiring at a specific call site
can override this fixture's effect for just that test by monkeypatching
the same target again inside the test itself (see e.g.
test_download_refuses_url_that_fails_the_ssrf_check in test_pipeline_default_extractor.py).
"""
import pytest


@pytest.fixture(autouse=True)
def _bypass_ssrf_check_for_synthetic_test_domains(monkeypatch, request):
    if "test_url_safety" in request.node.nodeid:
        return  # the one file that must exercise the real DNS-based check

    always_safe = lambda url: (True, "")
    # url_safety.is_safe_to_fetch is the one that actually matters: every
    # safe_get()-based call site (pipeline._download, RequestsPageFetcher,
    # discover_pdf_links, RobotsGate) routes through safe_get(), which
    # resolves is_safe_to_fetch via url_safety's OWN module namespace
    # internally -- patching a caller's imported reference to the name
    # has no effect on that internal lookup, only patching the source
    # module does. page_fetcher.is_safe_to_fetch is patched separately
    # because PlaywrightPageFetcher calls it directly (both for its
    # initial check and its route-interception validator), a genuinely
    # different call site that doesn't go through safe_get() at all.
    monkeypatch.setattr("polaris.scraper.url_safety.is_safe_to_fetch", always_safe)
    monkeypatch.setattr("polaris.scraper.page_fetcher.is_safe_to_fetch", always_safe, raising=False)
