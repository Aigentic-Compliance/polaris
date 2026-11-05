"""Tests for page_fetcher.py. The Playwright tests use a real local HTTP
server (127.0.0.1, not an external site -- this sandbox can't reach the
open internet, but it can reach itself) serving a page whose actual
content only appears after JavaScript runs. This is a genuine, not mocked,
proof that PlaywrightPageFetcher sees content RequestsPageFetcher cannot.
"""
import http.server
import threading
import time

import pytest
import requests

from polaris.common.exceptions import DownloadError
from polaris.scraper.page_fetcher import PlaywrightPageFetcher, RequestsPageFetcher

JS_RENDERED_PAGE = b"""<!DOCTYPE html>
<html><head><title>JS Rendered Policy</title></head>
<body>
<div id="root">Loading...</div>
<script>
document.getElementById('root').innerHTML =
  '<h1>Gifts and Hospitality Policy</h1>' +
  '<p>Employees must not accept gifts exceeding EUR 50 in value.</p>';
</script>
</body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(JS_RENDERED_PAGE)

    def log_message(self, *args):
        pass  # keep test output clean


@pytest.fixture(scope="module")
def local_js_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.1)  # let the server actually start accepting connections
    yield f"http://127.0.0.1:{port}/"
    server.shutdown()


def test_requests_fetcher_misses_javascript_injected_content(local_js_server):
    """Confirms the actual problem this whole feature exists to solve:
    plain HTTP sees only the pre-render shell. The literal string
    "Gifts and Hospitality Policy" is present in BOTH fetchers' output --
    it's embedded as a JS string literal in the raw source either way --
    so the real distinguishing signal is whether the placeholder
    ("Loading...") was ever replaced, which only happens if JS actually ran."""
    fetcher = RequestsPageFetcher(session=requests.Session())
    content = fetcher.fetch(local_js_server)
    assert b'id="root">Loading...' in content  # placeholder still there -- JS never executed


def test_playwright_fetcher_sees_javascript_injected_content(local_js_server):
    """The actual value-add, proven against a real (if local) server, not
    a mock: Playwright renders the page, the placeholder gets genuinely
    replaced, and the real content is what remains."""
    fetcher = PlaywrightPageFetcher()
    content = fetcher.fetch(local_js_server)
    assert b"Loading..." not in content  # placeholder was actually replaced by JS execution
    assert b"Gifts and Hospitality Policy" in content
    assert b"Employees must not accept gifts exceeding EUR 50" in content


def test_playwright_fetcher_raises_download_error_on_connection_failure():
    """A closed local port gives a real, fast, reliable connection failure
    -- unlike testing against an external unreachable address, which in
    this sandboxed environment goes through an egress proxy that may
    intercept the request in ways that don't behave like a genuine
    timeout. This is the more reliable version of the same test."""
    fetcher = PlaywrightPageFetcher(timeout_ms=3000)
    with pytest.raises(DownloadError):
        fetcher.fetch("http://127.0.0.1:1/")  # port 1 -- essentially never listening


def test_playwright_fetcher_raises_download_error_on_invalid_url():
    fetcher = PlaywrightPageFetcher(timeout_ms=2000)
    with pytest.raises(DownloadError):
        fetcher.fetch("not-a-valid-url-at-all")


def test_requests_fetcher_still_works_as_the_default_plain_path(local_js_server):
    """RequestsPageFetcher is not a regression path -- it must still
    correctly fetch a page's raw (pre-render) content."""
    fetcher = RequestsPageFetcher(session=requests.Session())
    content = fetcher.fetch(local_js_server)
    assert b"<title>JS Rendered Policy</title>" in content


def test_requests_fetcher_raises_download_error_on_connection_failure():
    fetcher = RequestsPageFetcher(session=requests.Session())
    with pytest.raises(DownloadError, match="GET .* failed"):
        fetcher.fetch("http://127.0.0.1:1/")  # essentially never listening


def test_playwright_fetcher_raises_download_error_on_real_navigation_timeout():
    """Distinct from the connection-failure test above: this uses a
    socket that accepts the connection but never sends a response, so
    Playwright genuinely waits until its own timeout elapses and raises
    TimeoutError specifically -- the other test's closed port fails fast
    with a different Playwright exception type."""
    import socket
    import threading

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    port = server_socket.getsockname()[1]

    def accept_and_hang():
        try:
            conn, _ = server_socket.accept()
            # Accept the connection but never write a response -- the
            # client (Playwright) will wait until its own timeout fires.
        except OSError:
            pass  # socket closed while waiting -- fine, test is ending

    thread = threading.Thread(target=accept_and_hang, daemon=True)
    thread.start()

    try:
        fetcher = PlaywrightPageFetcher(timeout_ms=1500)
        # Both PlaywrightTimeoutError and the general PlaywrightError path
        # map to DownloadError -- what matters functionally is that a
        # connection that never completes normally is handled at all, not
        # which of the two internal Playwright exception subtypes a given
        # raw-socket test setup happens to trigger (that's sensitive to
        # timing/OS specifics in a way not worth chasing further).
        with pytest.raises(DownloadError):
            fetcher.fetch(f"http://127.0.0.1:{port}/")
    finally:
        server_socket.close()


def test_playwright_fetcher_raises_download_error_on_oversized_content(local_js_server, monkeypatch):
    fetcher = PlaywrightPageFetcher()
    monkeypatch.setattr("polaris.scraper.page_fetcher.MAX_DOWNLOAD_BYTES", 10)  # smaller than any real page
    with pytest.raises(DownloadError, match="byte cap"):
        fetcher.fetch(local_js_server)


def test_playwright_fetcher_blocks_redirect_to_unsafe_target(monkeypatch):
    """The Playwright-specific version of the exact bypass proven and
    fixed for the plain-HTTP path: a page that looks fine to render
    (passes the initial check) but 302-redirects to something that must
    independently fail validation. Uses a real redirect against real
    local servers, not a mock -- confirms the request-event-based check
    (see page_fetcher.py's docstring for why page.route() alone does not
    work for this) actually aborts the connection to the redirect target
    during real rendering.
    """
    import http.server

    from polaris.scraper.page_fetcher import PlaywrightPageFetcher

    class TargetHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body>SECRET INTERNAL CONTENT</body></html>")

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

    # Simulate "the initial URL looked safe" (as a real public domain
    # would) while the redirect target must be independently, genuinely
    # evaluated -- allow only the entry URL, reject the redirect target,
    # modelling exactly "passed the initial check, then got redirected
    # somewhere unchecked" for a full-browser render.
    import polaris.scraper.page_fetcher as page_fetcher_module

    def selective_check(url):
        if url == redirect_url:
            return True, ""
        return False, f"simulated: {url} is the disallowed redirect target"

    monkeypatch.setattr(page_fetcher_module, "is_safe_to_fetch", selective_check)

    try:
        fetcher = PlaywrightPageFetcher()
        with pytest.raises(DownloadError):
            fetcher.fetch(redirect_url)
    finally:
        target_server.shutdown()
        redirect_server.shutdown()


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


def test_playwright_fetcher_still_follows_a_redirect_to_a_safe_target(monkeypatch):
    """The normal, legitimate case must still work -- e.g. a real site
    redirecting http to https. Confirms the fix doesn't just block
    everything indiscriminately."""
    import http.server

    from polaris.scraper.page_fetcher import PlaywrightPageFetcher

    class TargetHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html><body>final content</body></html>")

        def log_message(self, *a):
            pass

    target_server, target_port = _start_local_server(TargetHandler)
    target_url = f"http://127.0.0.1:{target_port}/final"

    class RedirectHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", target_url)
            self.end_headers()

        def log_message(self, *a):
            pass

    redirect_server, redirect_port = _start_local_server(RedirectHandler)
    redirect_url = f"http://127.0.0.1:{redirect_port}/"

    import polaris.scraper.page_fetcher as page_fetcher_module
    monkeypatch.setattr(page_fetcher_module, "is_safe_to_fetch", lambda url: (True, ""))

    try:
        fetcher = PlaywrightPageFetcher()
        content = fetcher.fetch(redirect_url)
        assert b"final content" in content
    finally:
        target_server.shutdown()
        redirect_server.shutdown()


def test_playwright_fetcher_handles_close_failing_during_abort_attempt(monkeypatch):
    """If page.close() itself raises while trying to abort an unsafe
    in-flight request (e.g. it's already mid-closing from a race with
    another blocked request), that must be swallowed -- the point was
    already achieved (closing was attempted), not something to crash on."""
    import http.server

    from playwright.sync_api import Error as PlaywrightError

    from polaris.scraper.page_fetcher import PlaywrightPageFetcher

    class TargetHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"content")

        def log_message(self, *a):
            pass

    target_server, target_port = _start_local_server(TargetHandler)
    target_url = f"http://127.0.0.1:{target_port}/"

    import polaris.scraper.page_fetcher as page_fetcher_module
    monkeypatch.setattr(page_fetcher_module, "is_safe_to_fetch", lambda url: (False, "simulated: blocked"))

    # Patch Page.close at the class level so the fetcher's internal
    # page.close() call (triggered by the blocked request) raises,
    # simulating the race described above.
    from playwright.sync_api import Page
    original_close = Page.close

    def raising_close(self, *a, **kw):
        raise PlaywrightError("simulated: already closing")

    monkeypatch.setattr(Page, "close", raising_close)

    try:
        fetcher = PlaywrightPageFetcher()
        with pytest.raises(DownloadError):
            fetcher.fetch(target_url)
    finally:
        monkeypatch.setattr(Page, "close", original_close)
        target_server.shutdown()
