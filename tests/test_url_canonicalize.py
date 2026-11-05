from polaris.scraper.url_canonicalize import canonicalize


def test_strips_utm_tracking_parameters():
    a = canonicalize("https://acme.com/policy.pdf?utm_source=twitter&utm_medium=social")
    b = canonicalize("https://acme.com/policy.pdf")
    assert a == b


def test_strips_multiple_different_tracking_params_to_same_result():
    a = canonicalize("https://acme.com/policy.pdf?fbclid=abc123")
    b = canonicalize("https://acme.com/policy.pdf?gclid=xyz789")
    c = canonicalize("https://acme.com/policy.pdf")
    assert a == b == c


def test_normalizes_host_case():
    a = canonicalize("https://ACME.com/policy.pdf")
    b = canonicalize("https://acme.com/policy.pdf")
    assert a == b


def test_removes_fragment():
    a = canonicalize("https://acme.com/policy.pdf#section-2")
    b = canonicalize("https://acme.com/policy.pdf")
    assert a == b


def test_sorts_remaining_meaningful_query_params_consistently():
    a = canonicalize("https://acme.com/search?topic=gifts&lang=en")
    b = canonicalize("https://acme.com/search?lang=en&topic=gifts")
    assert a == b


def test_keeps_meaningful_non_tracking_query_params():
    """Only known tracking params are stripped -- a real, meaningful query
    parameter (e.g. a language or version selector) must survive, since
    stripping it could silently merge two genuinely different documents."""
    result = canonicalize("https://acme.com/policy.pdf?lang=fr")
    assert "lang=fr" in result


def test_different_paths_are_not_conflated():
    a = canonicalize("https://acme.com/policy.pdf")
    b = canonicalize("https://acme.com/other-policy.pdf")
    assert a != b


def test_trailing_slash_is_not_stripped():
    """Deliberately conservative: some servers serve different content at
    a path with vs without a trailing slash. Under-canonicalizing here
    (missing an occasional true duplicate) is the safer failure mode than
    over-canonicalizing (silently merging two different documents)."""
    a = canonicalize("https://acme.com/policy/")
    b = canonicalize("https://acme.com/policy")
    assert a != b


def test_combination_of_tracking_params_case_and_fragment():
    a = canonicalize("https://ACME.com/Policy.pdf?utm_source=x&utm_campaign=y#top")
    b = canonicalize("https://acme.com/Policy.pdf")
    assert a == b
