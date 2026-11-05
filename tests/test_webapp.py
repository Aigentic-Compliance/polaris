"""Tests for the Flask search UI, using Flask's test client -- no real
server, no real network. Monkeypatches get_default_search_provider and
SourceRegistry the same way the CLI/pipeline tests do.
"""
import pymupdf as fitz
import pytest
import responses
from sqlalchemy import create_engine

from polaris.common.exceptions import SearchProviderError
from polaris.scraper.search_provider import SearchProvider, SearchResult
from polaris.scraper.source_registry import SourceRegistry
from polaris.webapp import app as webapp_module


@pytest.fixture
def client():
    webapp_module.app.config["TESTING"] = True
    return webapp_module.app.test_client()


def test_get_shows_the_search_form(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Policy Search" in resp.data
    assert b"<form" in resp.data


def test_empty_query_shows_validation_error_not_a_crash(client):
    resp = client.post("/", data={"query": "   "})
    assert resp.status_code == 200
    assert b"can&#39;t be empty" in resp.data or b"can't be empty" in resp.data


def test_no_search_provider_configured_shows_clear_message_not_a_crash(client, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    resp = client.post("/", data={"query": "data privacy policy"})
    assert resp.status_code == 200
    assert b"No search provider is configured" in resp.data


@responses.activate
def test_successful_search_renders_results_table(client, monkeypatch, tmp_path):
    def _make_pdf():
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Data Privacy Policy")
        page.insert_text((72, 120), "This policy explains how we handle personal data privacy.")
        return doc.tobytes()

    responses.add(responses.GET, "https://example-corp.com/robots.txt", status=404)
    responses.add(responses.GET, "https://example-corp.com/privacy.pdf", body=_make_pdf(), status=200)

    class FixedProvider(SearchProvider):
        def search(self, query, num_results=20):
            return [SearchResult(url="https://example-corp.com/privacy.pdf", title="", snippet="")]

    test_registry = SourceRegistry(engine=create_engine(f"sqlite:///{tmp_path}/test.db"))
    monkeypatch.setattr(webapp_module, "get_default_search_provider", lambda: FixedProvider())
    monkeypatch.setattr(webapp_module, "SourceRegistry", lambda: test_registry)

    resp = client.post("/", data={"query": "data privacy policy"})
    assert resp.status_code == 200
    assert b"accepted" in resp.data
    assert b"example-corp.com" in resp.data
    assert b"unconfirmed:" in resp.data  # organisation identity honestly shown as unconfirmed


def test_unexpected_error_is_caught_at_route_level_not_a_500(client, monkeypatch):
    def _raises(*a, **kw):
        raise RuntimeError("simulated unexpected failure")

    monkeypatch.setattr(webapp_module, "get_default_search_provider", _raises)
    resp = client.post("/", data={"query": "some policy"})
    assert resp.status_code == 200  # caught and rendered, not a 500
    assert b"Something went wrong" in resp.data


@responses.activate
def test_search_with_rejected_and_needs_review_results_displays_both_statuses(client, monkeypatch, tmp_path):
    def _make_unrelated_pdf():
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Employee Handbook")
        page.insert_text((72, 120), "This document covers vacation and sick leave policies.")
        return doc.tobytes()

    def _make_ambiguous_pdf():
        doc = fitz.open()
        p1 = doc.new_page()
        p1.insert_text((72, 72), "Code of Business Conduct")
        p2 = doc.new_page()
        p2.insert_text((72, 72), "Employees must register any data privacy incident above the threshold.")
        return doc.tobytes()

    responses.add(responses.GET, "https://corp-a.com/robots.txt", status=404)
    responses.add(responses.GET, "https://corp-a.com/handbook.pdf", body=_make_unrelated_pdf(), status=200)
    responses.add(responses.GET, "https://corp-b.com/robots.txt", status=404)
    responses.add(responses.GET, "https://corp-b.com/conduct.pdf", body=_make_ambiguous_pdf(), status=200)

    class FixedProvider(SearchProvider):
        def search(self, query, num_results=20):
            return [
                SearchResult(url="https://corp-a.com/handbook.pdf", title="", snippet=""),
                SearchResult(url="https://corp-b.com/conduct.pdf", title="", snippet=""),
            ]

    test_registry = SourceRegistry(engine=create_engine(f"sqlite:///{tmp_path}/test2.db"))
    monkeypatch.setattr(webapp_module, "get_default_search_provider", lambda: FixedProvider())
    monkeypatch.setattr(webapp_module, "SourceRegistry", lambda: test_registry)

    resp = client.post("/", data={"query": "data privacy policy"})
    assert resp.status_code == 200
    assert b"rejected" in resp.data
    assert b"needs review" in resp.data


@responses.activate
def test_classify_checkbox_shows_classified_segments_in_results(client, monkeypatch, tmp_path):
    def _make_pdf():
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Data Privacy Policy")
        page.insert_text((72, 120), "Employees must not share personal data without consent.")
        return doc.tobytes()

    responses.add(responses.GET, "https://privacy-corp.com/robots.txt", status=404)
    responses.add(responses.GET, "https://privacy-corp.com/privacy.pdf", body=_make_pdf(), status=200)

    class FixedProvider(SearchProvider):
        def search(self, query, num_results=20):
            return [SearchResult(url="https://privacy-corp.com/privacy.pdf", title="", snippet="")]

    test_registry = SourceRegistry(engine=create_engine(f"sqlite:///{tmp_path}/test3.db"))
    monkeypatch.setattr(webapp_module, "get_default_search_provider", lambda: FixedProvider())
    monkeypatch.setattr(webapp_module, "SourceRegistry", lambda: test_registry)

    resp = client.post("/", data={"query": "data privacy policy", "classify": "1"})
    assert resp.status_code == 200
    assert b"accepted" in resp.data
    assert b"class=\"segments\"" in resp.data
    assert b"personal data" in resp.data


@responses.activate
def test_without_classify_checkbox_no_segments_shown(client, monkeypatch, tmp_path):
    def _make_pdf():
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Data Privacy Policy")
        page.insert_text((72, 120), "Employees must not share personal data without consent.")
        return doc.tobytes()

    responses.add(responses.GET, "https://privacy-corp2.com/robots.txt", status=404)
    responses.add(responses.GET, "https://privacy-corp2.com/privacy.pdf", body=_make_pdf(), status=200)

    class FixedProvider(SearchProvider):
        def search(self, query, num_results=20):
            return [SearchResult(url="https://privacy-corp2.com/privacy.pdf", title="", snippet="")]

    test_registry = SourceRegistry(engine=create_engine(f"sqlite:///{tmp_path}/test4.db"))
    monkeypatch.setattr(webapp_module, "get_default_search_provider", lambda: FixedProvider())
    monkeypatch.setattr(webapp_module, "SourceRegistry", lambda: test_registry)

    resp = client.post("/", data={"query": "data privacy policy"})  # no "classify" key at all -- unchecked
    assert resp.status_code == 200
    assert b"accepted" in resp.data
    assert b"class=\"segments\"" not in resp.data
