"""Tests for AzureLETClassifier's retry and error-handling behaviour.
Mocks the raw API call (_call_api) rather than hitting a real Azure
endpoint -- exactly the seam left in llm_classifier.py for this purpose.
"""
import json
from unittest.mock import MagicMock

import httpx
import openai
import pytest

from polaris.common.exceptions import ClassificationError

# AzureLETClassifier.__init__ requires env vars and a real network call to
# construct the AzureOpenAI client attribute -- we bypass __init__ entirely
# with __new__ and set only what classify() actually touches, so these
# tests need no credentials and no network.
from polaris.classifier.llm_classifier import AzureLETClassifier


def _make_classifier() -> AzureLETClassifier:
    clf = AzureLETClassifier.__new__(AzureLETClassifier)
    clf.deployment = "test-deployment"
    clf._system_prompt = "test prompt"
    clf._client = MagicMock()
    return clf


def _fake_request():
    return httpx.Request("POST", "https://example.openai.azure.com/")


def _fake_response(status_code: int):
    return httpx.Response(status_code, request=_fake_request())


def _make_success_response(label="RUL", confidence=0.9, rationale="ok"):
    payload = json.dumps({"label": label, "confidence": confidence, "rationale": rationale})
    message = MagicMock()
    message.content = payload
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def test_transient_error_retries_then_succeeds(monkeypatch):
    clf = _make_classifier()
    calls = {"count": 0}

    def flaky_call(text):
        calls["count"] += 1
        if calls["count"] < 3:
            raise openai.APIConnectionError(request=_fake_request())
        return _make_success_response()

    monkeypatch.setattr(clf, "_call_api", flaky_call)
    # tenacity's wait_exponential would otherwise sleep for real between
    # retries in this test; not worth mocking time for 3 fast attempts at
    # min=1s, but if this test ever feels slow, that's why.
    result = clf.classify("some policy text")
    assert result.label == "RUL"
    assert calls["count"] == 3


def test_transient_error_exhausts_retries_raises_classification_error(monkeypatch):
    clf = _make_classifier()

    def always_fails(text):
        raise openai.APIConnectionError(request=_fake_request())

    monkeypatch.setattr(clf, "_call_api", always_fails)
    with pytest.raises(ClassificationError, match="failed after retries"):
        clf.classify("some policy text")


def test_non_transient_status_error_is_not_retried_raises_immediately(monkeypatch):
    clf = _make_classifier()
    calls = {"count": 0}

    def auth_failure(text):
        calls["count"] += 1
        raise openai.APIStatusError(
            "unauthorized", response=_fake_response(401), body={"error": "bad key"}
        )

    monkeypatch.setattr(clf, "_call_api", auth_failure)
    with pytest.raises(ClassificationError, match="status 401"):
        clf.classify("some policy text")
    assert calls["count"] == 1  # never retried


def test_malformed_json_response_raises_classification_error_not_fabricated_label(monkeypatch):
    clf = _make_classifier()

    def bad_json_call(text):
        message = MagicMock()
        message.content = "not valid json {{{"
        choice = MagicMock()
        choice.message = message
        response = MagicMock()
        response.choices = [choice]
        return response

    monkeypatch.setattr(clf, "_call_api", bad_json_call)
    with pytest.raises(ClassificationError, match="Could not parse"):
        clf.classify("some policy text")


def test_azure_classifier_constructs_with_env_vars(monkeypatch):
    """Verifies __init__ actually reads the documented env vars correctly
    -- construction only, no real network call (AzureOpenAI's constructor
    doesn't validate credentials against the live service)."""
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake-resource.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key-for-testing")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-test-deployment")

    clf = AzureLETClassifier()
    assert clf.deployment == "gpt-4o-test-deployment"
    assert clf._system_prompt  # built successfully


def test_azure_classifier_missing_required_env_var_raises_key_error(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    with pytest.raises(KeyError):
        AzureLETClassifier()


def test_get_default_classifier_returns_azure_when_key_present(monkeypatch):
    from polaris.classifier.llm_classifier import get_default_classifier

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake-resource.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "fake-key-for-testing")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-test-deployment")

    classifier = get_default_classifier()
    assert isinstance(classifier, AzureLETClassifier)


def test_get_default_classifier_returns_fallback_and_warns_when_no_key(monkeypatch):
    from polaris.classifier.llm_classifier import RuleBasedFallbackClassifier, get_default_classifier

    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    with pytest.warns(UserWarning, match="using RuleBasedFallbackClassifier"):
        classifier = get_default_classifier()
    assert isinstance(classifier, RuleBasedFallbackClassifier)
