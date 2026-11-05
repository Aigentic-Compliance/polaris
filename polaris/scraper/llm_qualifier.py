"""The LLM-assisted half of document qualification -- C6.3 in the WP1
framework: "Qualification proposer (classifier, LLM with criteria, or
hybrid) returning class, relevance, unit, evidence snippet with page, and
confidence -- never a bare yes/no."

document_validator.py's deterministic rules handle the two confident cases
well (a title that directly names the topic; a title that clearly matches
a known non-policy type like an annual report) -- those don't need an LLM
call, they're cheap and reliable as-is. This module exists specifically
for the case the rules can't resolve: no title signal either way, some
body keywords present. Before this existed, that case was always routed
straight to needs_review with no second opinion at all -- a human had to
resolve every single one by hand, with nothing helping them.

This does NOT replace the deterministic rules -- it is only consulted when
they're genuinely ambiguous, and its output is a PROPOSAL for the ambiguous
case, not an automatic override. Never call this to second-guess a
confident rule-based accept or reject; that would spend real API calls on
cases already resolved for free, and would make a confident decision
depend on a nondeterministic LLM call for no reason.

Failure modes: same pattern as llm_classifier.py -- retries only genuinely
transient errors (rate limit, timeout, connection error), never retries a
malformed response or auth failure, and never fabricates a class/confidence
to paper over a failure. Unlike the classification cascade, there IS a
safe fallback here: if no qualifier is configured, or the call fails, the
caller keeps the existing deterministic-rules outcome (needs_review)
unchanged -- this is strictly additive, never a regression from today's
behaviour.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from polaris.common.document_class import DOCUMENT_CLASS_REGISTRY, DocumentClass
from polaris.common.exceptions import QualificationError
from polaris.common.logging_config import get_logger

logger = get_logger(__name__)

PROMPT_VERSION = "qualifier_v1_2026-09-23"


@dataclass
class QualificationResult:
    document_class: str          # one of D1-D11
    relevance_level: str         # "none" | "mention" | "section" | "dedicated"
    unit: str                    # "whole_document" | "section"
    evidence_snippet: str
    confidence: float
    rationale: str
    model_id: str
    prompt_version: str | None = PROMPT_VERSION


class LLMQualifier(ABC):
    @abstractmethod
    def qualify(self, title_text: str, body_sample: str, topic: str, negative_hint: str | None = None) -> QualificationResult:
        ...


def _build_system_prompt(topic: str) -> str:
    lines = [
        f"You are a compliance-document qualifier. Decide whether the given document is a "
        f"policy source relevant to the topic '{topic}', using the document class vocabulary below.",
        "You are being consulted specifically because a simpler rule-based check could not decide "
        "confidently -- treat this as a genuinely ambiguous case, not an easy one.",
        "",
    ]
    for code, spec in DOCUMENT_CLASS_REGISTRY.items():
        lines.append(f"{code} = {spec.name}: e.g. {spec.example}. Default action: {spec.default_at_g0}.")
    lines.append("")
    lines.append(
        "relevance_level: 'none' (topic not addressed), 'mention' (topic named in passing), "
        "'section' (a dedicated section addresses the topic), 'dedicated' (the whole document is about the topic)."
    )
    lines.append(
        "unit: 'whole_document' if the entire file should be treated as one source, 'section' if only "
        "a specific section is relevant (e.g. a Code of Conduct with one gifts page)."
    )
    lines.append(
        "evidence_snippet: quote the specific short passage (a sentence or two) that most supports your "
        "document_class and relevance_level decision -- never invent one."
    )
    lines.append(
        "Never guess at full confidence. If the text gives genuinely mixed signals, say so and lower "
        "your confidence rather than picking a class arbitrarily."
    )
    return "\n".join(lines)


_RESPONSE_JSON_SCHEMA = {
    "name": "document_qualification",
    "schema": {
        "type": "object",
        "properties": {
            "document_class": {"type": "string", "enum": [c.value for c in DocumentClass]},
            "relevance_level": {"type": "string", "enum": ["none", "mention", "section", "dedicated"]},
            "unit": {"type": "string", "enum": ["whole_document", "section"]},
            "evidence_snippet": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "rationale": {"type": "string"},
        },
        "required": ["document_class", "relevance_level", "unit", "evidence_snippet", "confidence", "rationale"],
        "additionalProperties": False,
    },
    "strict": True,
}


class AzureLLMQualifier(LLMQualifier):
    """Requires the same AZURE_OPENAI_ENDPOINT/API_KEY/API_VERSION as
    llm_classifier.py's AzureLETClassifier -- same account, same approved
    provider, no new vendor decision needed for this. The deployment
    (which model) is configured independently, via
    AZURE_OPENAI_QUALIFIER_DEPLOYMENT, falling back to
    AZURE_OPENAI_DEPLOYMENT if that's not set.

    This separation matters in practice, not just in theory: the
    classifier and qualifier have very different cost/volume profiles --
    one call per paragraph-sized segment (high volume, well-specified
    18-way classification) versus one call only for genuinely ambiguous
    documents (low volume, more nuanced judgment across an 11-way class
    plus a confidence and evidence snippet). Before this, both were
    hard-wired to the same deployment name, with no way to point a cheap,
    high-volume-appropriate model at one and a stronger model at the
    other without duplicating environment configuration entirely.
    """

    def __init__(self):
        from openai import AzureOpenAI

        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        api_key = os.environ["AZURE_OPENAI_API_KEY"]
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
        self.deployment = os.environ.get("AZURE_OPENAI_QUALIFIER_DEPLOYMENT") or os.environ["AZURE_OPENAI_DEPLOYMENT"]
        self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)

    def _call_api(self, user_content: str, system_prompt: str):
        return self._client.chat.completions.create(
            model=self.deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_schema", "json_schema": _RESPONSE_JSON_SCHEMA},
            temperature=0,
        )

    def qualify(self, title_text: str, body_sample: str, topic: str, negative_hint: str | None = None) -> QualificationResult:
        import openai
        from polaris.common.injection_guard import wrap_untrusted_text

        system_prompt = _build_system_prompt(topic)
        # Each piece of scraped text is individually wrapped with explicit
        # untrusted-data framing (see injection_guard.py) rather than
        # wrapping the whole assembled user_content once -- this keeps the
        # "which part is scraped text vs. this module's own labelling"
        # boundary unambiguous even if title_text or body_sample itself
        # contains delimiter-like text.
        user_content = (
            f"Title / first page text:\n{wrap_untrusted_text(title_text, label='TITLE TEXT')}\n\n"
            f"Body sample:\n{wrap_untrusted_text(body_sample, label='BODY SAMPLE')}\n\n"
            + (f"Note: the title also partially matches a known non-policy document type "
               f"('{negative_hint}') -- weigh this carefully.\n" if negative_hint else "")
        )

        retrying_call = retry(
            retry=retry_if_exception_type((openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            reraise=True,
            before_sleep=lambda retry_state: logger.warning(
                "Azure OpenAI qualification call attempt %d failed (%s); retrying...",
                retry_state.attempt_number, retry_state.outcome.exception(),
            ),
        )(self._call_api)

        try:
            response = retrying_call(user_content, system_prompt)
        except (openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError) as exc:
            logger.error("Azure OpenAI qualification call failed after retries: %s", exc)
            raise QualificationError(f"Qualification call failed after retries: {exc}") from exc
        except openai.APIStatusError as exc:
            logger.error("Azure OpenAI qualification call failed (status %s, non-retryable): %s", exc.status_code, exc)
            raise QualificationError(f"Qualification call failed (status {exc.status_code}): {exc}") from exc

        try:
            payload = json.loads(response.choices[0].message.content)
            return QualificationResult(
                document_class=payload["document_class"],
                relevance_level=payload["relevance_level"],
                unit=payload["unit"],
                evidence_snippet=payload["evidence_snippet"],
                confidence=float(payload["confidence"]),
                rationale=payload["rationale"],
                model_id=f"azure-openai:{self.deployment}",
            )
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("Azure OpenAI qualification response failed to parse as expected: %s", exc)
            raise QualificationError(f"Could not parse qualification response: {exc}") from exc


def get_default_qualifier() -> LLMQualifier | None:
    """Unlike get_default_search_provider(), this has a safe, meaningful
    fallback -- return None, and let the caller keep the existing
    deterministic-rules outcome unchanged. No warning is logged for the
    unconfigured case at this level; document_validator.py's caller
    decides whether that's worth surfacing, since running without this is
    a legitimate, unregressed mode, not a degraded one the way the
    classification cascade's rule-based fallback is."""
    if os.environ.get("AZURE_OPENAI_API_KEY"):
        return AzureLLMQualifier()
    return None
