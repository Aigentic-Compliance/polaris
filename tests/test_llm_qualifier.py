"""Tests for llm_qualifier.py, using the same monkeypatch-the-raw-call
pattern as test_llm_classifier_error_handling.py -- no real network, no
real credentials, real retry/error-handling logic exercised directly.
"""
import json
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from polaris.common.exceptions import QualificationError
from polaris.scraper.llm_qualifier import AzureLLMQualifier, get_default_qualifier


def _make_qualifier() -> AzureLLMQualifier:
    q = AzureLLMQualifier.__new__(AzureLLMQualifier)
    q.deployment = "test-deployment"
    q._client = MagicMock()
    return q


def _fake_request():
    return httpx.Request("POST", "https://example.openai.azure.com/")


def _fake_response(status_code):
    return httpx.Response(status_code, request=_fake_request())


def _make_success_response(**overrides):
    payload = {
        "document_class": "D1", "relevance_level": "dedicated", "unit": "whole_document",
        "evidence_snippet": "This policy governs gift-giving.", "confidence": 0.9,
        "rationale": "Directly addresses the topic.",
    }
    payload.update(overrides)
    message = MagicMock()
    message.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def test_successful_qualification_returns_expected_fields(monkeypatch):
    q = _make_qualifier()
    monkeypatch.setattr(q, "_call_api", lambda *a, **kw: _make_success_response())
    result = q.qualify("Some Title", "Some body text", "GFT")
    assert result.document_class == "D1"
    assert result.relevance_level == "dedicated"
    assert result.confidence == 0.9


def test_transient_error_retries_then_succeeds(monkeypatch):
    q = _make_qualifier()
    calls = {"count": 0}

    def flaky(*a, **kw):
        calls["count"] += 1
        if calls["count"] < 3:
            raise openai.APIConnectionError(request=_fake_request())
        return _make_success_response()

    monkeypatch.setattr(q, "_call_api", flaky)
    result = q.qualify("Title", "Body", "GFT")
    assert result.document_class == "D1"
    assert calls["count"] == 3


def test_non_transient_error_not_retried(monkeypatch):
    q = _make_qualifier()
    calls = {"count": 0}

    def auth_failure(*a, **kw):
        calls["count"] += 1
        raise openai.APIStatusError("unauthorized", response=_fake_response(401), body={"error": "bad key"})

    monkeypatch.setattr(q, "_call_api", auth_failure)
    with pytest.raises(QualificationError, match="status 401"):
        q.qualify("Title", "Body", "GFT")
    assert calls["count"] == 1


def test_malformed_response_raises_qualification_error_not_fabricated_result(monkeypatch):
    q = _make_qualifier()

    def bad_json(*a, **kw):
        message = MagicMock()
        message.content = "not valid json {{{"
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        return response

    monkeypatch.setattr(q, "_call_api", bad_json)
    with pytest.raises(QualificationError, match="Could not parse"):
        q.qualify("Title", "Body", "GFT")


def test_get_default_qualifier_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    assert get_default_qualifier() is None


def test_get_default_qualifier_returns_real_qualifier_when_configured(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-test")
    qualifier = get_default_qualifier()
    assert isinstance(qualifier, AzureLLMQualifier)


def test_transient_error_exhausts_retries_raises_qualification_error(monkeypatch):
    q = _make_qualifier()

    def always_fails(*a, **kw):
        raise openai.APIConnectionError(request=_fake_request())

    monkeypatch.setattr(q, "_call_api", always_fails)
    with pytest.raises(QualificationError, match="failed after retries"):
        q.qualify("Title", "Body", "GFT")


def test_all_codes_returns_all_eleven_classes():
    from polaris.common.document_class import all_codes
    codes = all_codes()
    assert len(codes) == 11
    assert "D1" in codes and "D11" in codes


def test_qualifier_falls_back_to_shared_deployment_when_no_dedicated_one_set(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "shared-deployment")
    monkeypatch.delenv("AZURE_OPENAI_QUALIFIER_DEPLOYMENT", raising=False)
    qualifier = AzureLLMQualifier()
    assert qualifier.deployment == "shared-deployment"


def test_qualifier_uses_dedicated_deployment_when_set(monkeypatch):
    """The actual point of this configuration: point the low-volume,
    higher-stakes qualifier at a different (e.g. stronger) model than
    the high-volume paragraph classifier, without duplicating every
    other Azure env var."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "classifier-deployment")
    monkeypatch.setenv("AZURE_OPENAI_QUALIFIER_DEPLOYMENT", "qualifier-deployment")
    qualifier = AzureLLMQualifier()
    assert qualifier.deployment == "qualifier-deployment"
