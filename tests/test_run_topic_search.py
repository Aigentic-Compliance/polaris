"""End-to-end tests for run_topic_search -- the actual "find policies of
this type across the internet" capability, distinct from
run_for_organisation's "check this one known site" capability.
"""
import pytest
import pymupdf as fitz
import responses

from polaris.common.exceptions import SearchProviderError
from polaris.scraper.pipeline import UNCONFIRMED_ORG_PREFIX, run_topic_search
from polaris.scraper.search_provider import SearchProvider, SearchResult
from polaris.scraper.source_registry import SourceRegistry
from sqlalchemy import create_engine


def _make_real_gift_policy_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Global Gifts and Hospitality Policy")
    page.insert_text((72, 120), "Employees must not accept gifts exceeding EUR 50 in value.")
    return doc.tobytes()


class _FixedResultsProvider(SearchProvider):
    def __init__(self, results):
        self._results = results

    def search(self, query, num_results=20):
        return self._results


@responses.activate
def test_run_topic_search_finds_and_accepts_a_real_policy_across_an_unknown_domain():
    real_pdf = _make_real_gift_policy_pdf_bytes()
    responses.add(responses.GET, "https://never-seen-before-corp.com/robots.txt", status=404)
    responses.add(responses.GET, "https://never-seen-before-corp.com/policy.pdf", body=real_pdf, status=200)

    provider = _FixedResultsProvider([
        SearchResult(url="https://never-seen-before-corp.com/policy.pdf", title="Gifts Policy", snippet=""),
    ])
    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))

    result = run_topic_search("GFT", provider, registry)

    assert result.downloaded == 1
    assert result.accepted == 1
    docs = registry.all_accepted()
    assert len(docs) == 1
    # Organisation identity is NOT guessed -- explicitly marked unconfirmed.
    assert docs[0]["organisation"] == f"{UNCONFIRMED_ORG_PREFIX}never-seen-before-corp.com"


@responses.activate
def test_run_topic_search_counts_distinct_domains():
    def _make_pdf(text_suffix):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Global Gifts and Hospitality Policy")
        page.insert_text((72, 120), f"Employees must not accept gifts exceeding EUR 50 in value. {text_suffix}")
        return doc.tobytes()

    for domain, suffix in [("corp-a.com", "Variant A"), ("corp-b.com", "Variant B")]:
        responses.add(responses.GET, f"https://{domain}/robots.txt", status=404)
        responses.add(responses.GET, f"https://{domain}/policy.pdf", body=_make_pdf(suffix), status=200)

    provider = _FixedResultsProvider([
        SearchResult(url="https://corp-a.com/policy.pdf", title="", snippet=""),
        SearchResult(url="https://corp-b.com/policy.pdf", title="", snippet=""),
    ])
    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    result = run_topic_search("GFT", provider, registry)
    assert result.distinct_domains_found == 2
    assert result.accepted == 2


def test_run_topic_search_propagates_search_provider_error_when_provider_itself_fails():
    """If every query fails, discover_via_search now raises
    SearchProviderError (see discovery.py) rather than silently returning
    an empty candidate list -- run_topic_search must let that propagate."""
    class AlwaysFailsProvider(SearchProvider):
        def search(self, query, num_results=20):
            raise SearchProviderError("simulated: no credentials")

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    with pytest.raises(SearchProviderError):
        run_topic_search("GFT", AlwaysFailsProvider(), registry)



def test_run_free_text_search_finds_and_accepts_using_typed_phrase():
    """The actual capability requested: type any policy type as free
    text, not a pre-registered topic code, and have it drive discovery
    and acceptance."""
    from polaris.scraper.pipeline import run_free_text_search

    def _make_privacy_policy_pdf() -> bytes:
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Data Privacy Policy")
        page.insert_text((72, 120), "This policy explains how we handle personal data privacy.")
        return doc.tobytes()

    pdf_bytes = _make_privacy_policy_pdf()

    import responses as responses_lib
    with responses_lib.RequestsMock() as rsps:
        rsps.add(responses_lib.GET, "https://some-company.com/robots.txt", status=404)
        rsps.add(responses_lib.GET, "https://some-company.com/data-privacy-policy.pdf", body=pdf_bytes, status=200)

        provider = _FixedResultsProvider([
            SearchResult(url="https://some-company.com/data-privacy-policy.pdf", title="", snippet=""),
        ])
        registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
        result = run_free_text_search("data privacy policy", provider, registry)

    assert result.topic == "FREE:data-privacy-policy"
    assert result.accepted == 1
    docs = registry.all_accepted()
    assert "data privacy" in docs[0]["acceptance_reason"].lower()


def test_run_free_text_search_rejects_empty_query():
    from polaris.scraper.pipeline import run_free_text_search

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    with pytest.raises(ValueError, match="must not be empty"):
        run_free_text_search("   ", _FixedResultsProvider([]), registry)
