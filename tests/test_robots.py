import responses

from polaris.scraper.robots import RobotsGate


@responses.activate
def test_disallowed_path_is_blocked():
    responses.add(
        responses.GET, "https://example.com/robots.txt",
        body="User-agent: *\nDisallow: /private/\n", status=200,
    )
    gate = RobotsGate()
    assert gate.allowed("https://example.com/public/page") is True
    assert gate.allowed("https://example.com/private/page") is False


@responses.activate
def test_missing_robots_txt_defaults_to_allow():
    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    gate = RobotsGate()
    assert gate.allowed("https://example.com/anything") is True


@responses.activate
def test_any_4xx_defaults_to_allow_not_just_404():
    """WP1 framework C3.1 / RFC 9309: ANY 4xx means no restrictions apply,
    not only a clean 404. A 403 is not a disallow signal -- it means the
    crawler couldn't read robots.txt, which the standard treats as if the
    file doesn't exist. This is the bug fixed after the WP1 Target
    Capability & Acceptance Framework v0.2 made the rule explicit -- the
    prior version of robots.py only allowed on 404 and failed closed on
    every other non-200 status, including 403."""
    responses.add(responses.GET, "https://example.com/robots.txt", status=403)
    gate = RobotsGate()
    assert gate.allowed("https://example.com/anything") is True


@responses.activate
def test_5xx_fails_closed():
    responses.add(responses.GET, "https://example.com/robots.txt", status=500)
    gate = RobotsGate()
    # 5xx / unreachable must be treated as full disallow, per C3.1.
    assert gate.allowed("https://example.com/anything") is False


@responses.activate
def test_network_exception_fails_closed():
    import requests as requests_module
    responses.add(responses.GET, "https://example.com/robots.txt", body=requests_module.ConnectionError("simulated"))
    gate = RobotsGate()
    assert gate.allowed("https://example.com/anything") is False
