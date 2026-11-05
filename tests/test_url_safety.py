"""Tests for the SSRF defense -- every case is a real, concrete attack
pattern from the standard SSRF playbook, not a hypothetical.
"""
import pytest

from polaris.scraper.url_safety import is_safe_to_fetch


@pytest.mark.parametrize("url,description", [
    ("http://169.254.169.254/latest/meta-data/", "AWS/GCP/Azure cloud metadata endpoint"),
    ("http://169.254.169.254/latest/meta-data/iam/security-credentials/", "AWS credential exfiltration path"),
    ("http://127.0.0.1:6379/", "loopback, common internal service port"),
    ("http://localhost/admin", "loopback via hostname"),
    ("http://10.0.0.5/internal", "RFC 1918 private range (10.0.0.0/8)"),
    ("http://192.168.1.1/", "RFC 1918 private range (192.168.0.0/16)"),
    ("http://172.16.0.1/", "RFC 1918 private range (172.16.0.0/12)"),
    ("http://0.0.0.0/", "unspecified address"),
    ("http://[::1]/", "IPv6 loopback"),
])
def test_blocks_known_ssrf_targets(url, description):
    safe, reason = is_safe_to_fetch(url)
    assert safe is False, f"should have blocked {description}: {url}"
    assert reason  # a real reason string, not silently False with nothing to log


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://example.com/",
    "ftp://example.com/file.pdf",
    "data:text/html,<script>alert(1)</script>",
])
def test_blocks_non_http_schemes(url):
    safe, reason = is_safe_to_fetch(url)
    assert safe is False
    assert "not http/https" in reason or "no hostname" in reason


def test_allows_real_public_addresses():
    safe, reason = is_safe_to_fetch("http://8.8.8.8/")
    assert safe is True
    assert reason == ""


def test_allows_real_public_hostname():
    safe, reason = is_safe_to_fetch("https://example.com/policy.pdf")
    assert safe is True


def test_blocks_url_with_no_hostname():
    safe, reason = is_safe_to_fetch("http:///path-only")
    assert safe is False
    assert "no hostname" in reason


def test_handles_unresolvable_hostname_gracefully():
    safe, reason = is_safe_to_fetch("http://this-domain-absolutely-does-not-exist-12345.invalid/")
    assert safe is False
    assert "could not resolve" in reason


def test_never_raises_on_malformed_url():
    """A validator that itself crashes on bad input is worse than useless
    -- it must always return a clean (False, reason), never propagate."""
    for bad_url in ["", "not a url at all", "http://", "://missing-scheme"]:
        safe, reason = is_safe_to_fetch(bad_url)
        assert safe is False


# --- Integration tests: confirm every real call site actually wires this
# in, not just that url_safety.py works in isolation. Each test overrides
# conftest.py's blanket "always safe" test fixture for just this one test,
# to prove the real wiring refuses an unsafe URL end to end.

def test_pipeline_download_refuses_unsafe_url(monkeypatch):
    """_download() now routes through safe_get() (url_safety.py), which
    validates via url_safety's own module-level is_safe_to_fetch -- not
    a separate pre-check pipeline.py does itself anymore. Patching that
    directly, rather than a stale reference, is what makes this test
    actually exercise the real current code path."""
    import polaris.scraper.pipeline as pipeline_module
    import polaris.scraper.url_safety as url_safety_module
    from polaris.common.exceptions import DownloadError

    monkeypatch.setattr(url_safety_module, "is_safe_to_fetch", lambda url: (False, "simulated: blocked"))
    with pytest.raises(DownloadError, match="Refused to fetch"):
        pipeline_module._download(session=None, url="http://169.254.169.254/")


def test_requests_page_fetcher_refuses_unsafe_url(monkeypatch):
    """RequestsPageFetcher now routes through safe_get() (url_safety.py),
    same as pipeline._download -- patching url_safety's own
    is_safe_to_fetch is what actually reaches safe_get()'s internal check."""
    import polaris.scraper.url_safety as url_safety_module
    from polaris.common.exceptions import DownloadError
    from polaris.scraper.page_fetcher import RequestsPageFetcher

    monkeypatch.setattr(url_safety_module, "is_safe_to_fetch", lambda url: (False, "simulated: blocked"))
    fetcher = RequestsPageFetcher(session=None)
    with pytest.raises(DownloadError, match="Refused to fetch"):
        fetcher.fetch("http://169.254.169.254/")


def test_playwright_page_fetcher_refuses_unsafe_url(monkeypatch):
    import polaris.scraper.page_fetcher as page_fetcher_module
    from polaris.common.exceptions import DownloadError
    from polaris.scraper.page_fetcher import PlaywrightPageFetcher

    monkeypatch.setattr(page_fetcher_module, "is_safe_to_fetch", lambda url: (False, "simulated: blocked"))
    fetcher = PlaywrightPageFetcher()
    with pytest.raises(DownloadError, match="Refused to fetch"):
        fetcher.fetch("http://169.254.169.254/")


def test_discover_pdf_links_refuses_unsafe_seed_page(monkeypatch):
    """discover_pdf_links now routes through safe_get() too -- same
    reasoning as the RequestsPageFetcher test above."""
    import polaris.scraper.url_safety as url_safety_module
    from polaris.scraper.discovery import discover_pdf_links
    from polaris.scraper.robots import RobotsGate

    monkeypatch.setattr(url_safety_module, "is_safe_to_fetch", lambda url: (False, "simulated: blocked"))

    class AllowAllRobots(RobotsGate):
        def allowed(self, url, user_agent=None):
            return True

    result = discover_pdf_links("http://169.254.169.254/", session=None, robots=AllowAllRobots())
    assert result == []  # refused before ever attempting the fetch, not a crash


def test_robots_gate_fails_closed_on_unsafe_robots_url(monkeypatch):
    """RobotsGate now routes through safe_get() too -- same reasoning."""
    import polaris.scraper.url_safety as url_safety_module
    from polaris.scraper.robots import RobotsGate

    monkeypatch.setattr(url_safety_module, "is_safe_to_fetch", lambda url: (False, "simulated: blocked"))
    gate = RobotsGate()
    # Fails closed exactly like a network failure or 5xx would -- full disallow.
    assert gate.allowed("http://169.254.169.254/anything") is False


def test_handles_unparseable_resolved_address_gracefully(monkeypatch):
    """Extremely unlikely in practice (socket.getaddrinfo should always
    return valid IP strings), but defensive: a malformed resolved address
    must fail closed (False), never raise past this function."""
    import socket

    def fake_getaddrinfo(host, port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-a-valid-ip", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    safe, reason = is_safe_to_fetch("https://example.com/")
    assert safe is False
    assert "could not parse" in reason


# --- safe_get(): closes the redirect-bypass gap plain requests.Session.get()
# has -- proven exploitable with two real local servers before this fix.

def _start_local_server(handler_class):
    import http.server
    import threading
    import time
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_class)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)
    return server, port


def test_safe_get_follows_a_redirect_to_a_validated_safe_target(monkeypatch):
    """The normal, legitimate case must still work -- e.g. a real site
    redirecting http to https, or from a bare domain to www."""
    import http.server

    import requests

    from polaris.scraper.url_safety import safe_get

    class TargetHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"final content")

        def log_message(self, *a):
            pass

    target_server, target_port = _start_local_server(TargetHandler)

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target_port}/final")
            self.end_headers()

        def log_message(self, *a):
            pass

    redirect_server, redirect_port = _start_local_server(RedirectHandler)

    # Both servers are on loopback, which the REAL is_safe_to_fetch would
    # reject -- monkeypatch it to allow loopback for JUST this test, so
    # the actual thing under test (does safe_get re-validate each hop
    # and correctly ALLOW a validated redirect) is isolated from the
    # unrelated fact that loopback itself is normally blocked.
    import polaris.scraper.url_safety as url_safety_module
    real_check = url_safety_module.is_safe_to_fetch
    monkeypatch.setattr(url_safety_module, "is_safe_to_fetch", lambda url: (True, ""))

    try:
        resp = safe_get(requests.Session(), f"http://127.0.0.1:{redirect_port}/")
        assert resp.status_code == 200
        assert resp.text == "final content"
    finally:
        target_server.shutdown()
        redirect_server.shutdown()


def test_safe_get_blocks_redirect_to_unsafe_target_even_when_initial_url_is_safe(monkeypatch):
    """THE actual regression test for the bypass proven before this fix:
    the initial URL passes validation, but the server redirects somewhere
    that must independently fail validation -- and must be blocked."""
    import http.server

    import requests

    from polaris.scraper.url_safety import UnsafeURLError, safe_get

    class TargetHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"SECRET INTERNAL DATA")

        def log_message(self, *a):
            pass

    target_server, target_port = _start_local_server(TargetHandler)
    target_url = f"http://127.0.0.1:{target_port}/secret"

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        def log_message(self, *a):
            pass

    redirect_server, redirect_port = _start_local_server(RedirectHandler)
    redirect_url = f"http://127.0.0.1:{redirect_port}/"

    # Simulate "the initial URL looked safe" (as it would for a real public
    # domain) while the redirect target is genuinely evaluated for real --
    # allow only the FIRST url, reject everything else, exactly modelling
    # "passed the initial check, then got redirected somewhere unchecked."
    import polaris.scraper.url_safety as url_safety_module
    def selective_check(url):
        if url == redirect_url:
            return True, ""
        return False, f"simulated: {url} is the disallowed redirect target"
    monkeypatch.setattr(url_safety_module, "is_safe_to_fetch", selective_check)

    try:
        with pytest.raises(UnsafeURLError, match="redirect to"):
            safe_get(requests.Session(), redirect_url)
    finally:
        target_server.shutdown()
        redirect_server.shutdown()


def test_safe_get_raises_on_unsafe_initial_url_without_making_any_request(monkeypatch):
    import requests

    from polaris.scraper.url_safety import UnsafeURLError, safe_get

    with pytest.raises(UnsafeURLError):
        safe_get(requests.Session(), "http://169.254.169.254/latest/meta-data/")


def test_safe_get_raises_too_many_redirects_on_a_redirect_loop(monkeypatch):
    import http.server

    import requests

    from polaris.scraper.url_safety import safe_get

    class LoopHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", self.path + "x")  # always a "new" URL, never resolves
            self.end_headers()

        def log_message(self, *a):
            pass

    server, port = _start_local_server(LoopHandler)
    import polaris.scraper.url_safety as url_safety_module
    monkeypatch.setattr(url_safety_module, "is_safe_to_fetch", lambda url: (True, ""))

    try:
        with pytest.raises(requests.TooManyRedirects):
            safe_get(requests.Session(), f"http://127.0.0.1:{port}/")
    finally:
        server.shutdown()


def test_a_redirect_with_no_location_header_is_not_treated_as_a_redirect_by_requests(monkeypatch):
    """Documents WHY safe_get() has no "missing Location header" branch:
    requests.Response.is_redirect is itself defined as requiring a
    Location header to be present, so a 3xx-with-no-Location response
    never enters safe_get()'s redirect-following branch at all -- it
    falls straight through to the normal return, same as any other
    non-redirect response. This is requests' own behavior, not something
    safe_get() has to account for separately.
    """
    import http.server

    import requests

    class NoLocationHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.end_headers()  # no Location header at all

        def log_message(self, *a):
            pass

    server, port = _start_local_server(NoLocationHandler)
    try:
        resp = requests.get(f"http://127.0.0.1:{port}/", allow_redirects=False)
        assert resp.status_code == 302
        assert resp.is_redirect is False  # confirms the premise safe_get() relies on
    finally:
        server.shutdown()
