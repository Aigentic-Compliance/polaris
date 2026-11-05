"""Pass 2 of the cascade: single structured LLM call per segment.

This is deliberately the *only* place a trained/tunable model touches the
labelling decision, and deliberately not a trained model -- per the decision
with Mada, we are not standing up a labeller service or fine-tuning a
transformer for v1. One structured call to Azure OpenAI gpt-4o (the
project's existing settled model choice, already used for the Claudia
persona and evaluator scoring in Policy Cards) returns a JSON object
constrained to the 18-LET enum, a confidence score, and a one-line
rationale for reviewers.

Two implementations are provided:
  - AzureLETClassifier: the real thing, requires AZURE_OPENAI_* env vars.
  - RuleBasedFallbackClassifier: a deterministic, dependency-free classifier
    used by every test in this repo and by anyone running the pipeline
    before they have Azure credentials configured. It is intentionally
    crude (regex per LET) -- it exists to prove the cascade's plumbing
    works end-to-end, NOT as a claim about classification quality. Do not
    use it to produce anything you'd show Claudia as a real result.

Failure modes: AzureLETClassifier.classify() retries transient failures
(rate limit, timeout, connection error) up to 3 times with exponential
backoff via tenacity -- these are exactly the errors a retry can fix and
the errors urllib3's Retry (used elsewhere in this repo for plain HTTP)
doesn't cover, since this call goes through the openai SDK, not requests.
Non-transient failures (auth error, a response that fails JSON parsing or
schema validation) are NOT retried -- retrying the same bad input won't
produce a different result -- and are raised as ClassificationError after
being logged with enough context to diagnose. Nothing in this class ever
returns a guessed label to paper over a failed call; a caller that wants a
graceful degradation path (route to human review instead of crashing the
whole batch) implements that at the cascade.py level, where it can be a
visible, logged decision instead of a hidden one here.
"""

from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from polaris.common.exceptions import ClassificationError
from polaris.common.let_taxonomy import LET, LET_REGISTRY
from polaris.common.logging_config import get_logger

logger = get_logger(__name__)

PROMPT_VERSION = "let_v1_2026-09-17"


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    rationale: str
    model_id: str
    prompt_version: str | None = PROMPT_VERSION


class LETClassifier(ABC):
    @abstractmethod
    def classify(self, text: str) -> ClassificationResult:
        ...


def _build_system_prompt() -> str:
    lines = [
        "You are a compliance-policy paragraph classifier. Classify the given "
        "passage into exactly one of these 18 Library Entity Types (LETs). "
        "Return NOL if the passage is not substantive policy content "
        "(navigation, boilerplate, page furniture).",
        "",
    ]
    for code, spec in LET_REGISTRY.items():
        lines.append(f"{code} = {spec.name}: {spec.description}")
    lines.append("")
    lines.append(
        "Never invent a threshold, rule or definition that is not stated in the "
        "passage. If the passage is ambiguous between two labels, pick the more "
        "specific one (e.g. THR over RUL for a numeric threshold) and lower your "
        "confidence accordingly rather than guessing at full confidence."
    )
    return "\n".join(lines)


_RESPONSE_JSON_SCHEMA = {
    "name": "let_classification",
    "schema": {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": [c.value for c in LET]},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "rationale": {"type": "string"},
        },
        "required": ["label", "confidence", "rationale"],
        "additionalProperties": False,
    },
    "strict": True,
}


class AzureLETClassifier(LETClassifier):
    """Real classifier. Requires:
        AZURE_OPENAI_ENDPOINT
        AZURE_OPENAI_API_KEY
        AZURE_OPENAI_DEPLOYMENT   (the gpt-4o deployment name, not the base model name)
        AZURE_OPENAI_API_VERSION  (defaults to 2024-08-01-preview if unset)

    This is the "Labeller / Meta-Data Extractor" entry in
    ProtoFocus_AI_Model_Architecture.docx, section 2 (third-party foundation
    models) -- current, unmodified, prompt-engineering only. It is NOT the
    "Structured Extraction Model (v2)" in that document's section 3
    (fine-tuned XLM-RoBERTa) -- that model does not exist; see
    MODEL_STATUS.md. This call currently returns label + confidence +
    rationale only, not the threshold/role/timing parameter extraction the
    architecture doc's description implies is part of the same stage --
    see MODEL_STATUS.md "open question" before extending this schema.
    """

    def __init__(self):
        from openai import AzureOpenAI  # imported lazily so the package is optional at import time

        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
        api_key = os.environ["AZURE_OPENAI_API_KEY"]
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview")
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT"]
        self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
        self._system_prompt = _build_system_prompt()

    def _call_api(self, text: str):
        """The raw API call, retried by classify() below via tenacity. Kept
        as its own method (rather than inlined) so a test can monkeypatch
        it directly without needing a real Azure endpoint. text is wrapped
        with explicit untrusted-data framing (see injection_guard.py)
        before being placed in the user message -- see C12.4 in the WP1
        framework; scraped document text is data to classify, never
        instructions the model should follow."""
        from polaris.common.injection_guard import wrap_untrusted_text

        return self._client.chat.completions.create(
            model=self.deployment,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": wrap_untrusted_text(text, label="SEGMENT TEXT")},
            ],
            response_format={"type": "json_schema", "json_schema": _RESPONSE_JSON_SCHEMA},
            temperature=0,
        )

    def classify(self, text: str) -> ClassificationResult:
        import openai

        retrying_call = retry(
            retry=retry_if_exception_type((openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            reraise=True,
            before_sleep=lambda retry_state: logger.warning(
                "Azure OpenAI call attempt %d failed (%s); retrying...",
                retry_state.attempt_number, retry_state.outcome.exception(),
            ),
        )(self._call_api)

        try:
            response = retrying_call(text)
        except (openai.APIConnectionError, openai.RateLimitError, openai.APITimeoutError) as exc:
            logger.error("Azure OpenAI call failed after 3 attempts (transient error): %s", exc)
            raise ClassificationError(f"Azure OpenAI call failed after retries: {exc}") from exc
        except openai.APIStatusError as exc:
            # Non-transient (e.g. 401 auth, 400 bad request) -- never
            # retried, since the same input will fail identically again.
            logger.error("Azure OpenAI call failed (status %s, non-retryable): %s", exc.status_code, exc)
            raise ClassificationError(f"Azure OpenAI call failed (status {exc.status_code}): {exc}") from exc

        try:
            payload = json.loads(response.choices[0].message.content)
            label = payload["label"]
            confidence = float(payload["confidence"])
            rationale = payload["rationale"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
            # The JSON schema is "strict", so this should be rare, but a
            # provider-side change or an empty/refused completion is still
            # possible. Never fall back to a plausible-looking default label
            # here -- that is exactly the fabricated-value failure mode this
            # repo's error-handling rules forbid.
            logger.error("Azure OpenAI returned a response that failed to parse as expected: %s", exc)
            raise ClassificationError(f"Could not parse classifier response: {exc}") from exc

        return ClassificationResult(
            label=label,
            confidence=confidence,
            rationale=rationale,
            model_id=f"azure-openai:{self.deployment}",
            prompt_version=PROMPT_VERSION,
        )


class RuleBasedFallbackClassifier(LETClassifier):
    """Deterministic, no-network classifier for tests and offline dry-runs.
    See module docstring -- this is a plumbing fixture, not a quality claim.
    """

    _PATTERNS: list[tuple[str, re.Pattern]] = [
        ("THR", re.compile(r"\b(eur|usd|gbp|\$|€|£)\s?\d|\d+\s?(eur|usd|gbp|%)", re.IGNORECASE)),
        ("OPA", re.compile(r"\bapproval\b|\bapprove[ds]?\b", re.IGNORECASE)),
        ("OPR", re.compile(r"\bregist(er|ration)\b", re.IGNORECASE)),
        ("OPW", re.compile(r"\bwaiver\b|\bexception\b", re.IGNORECASE)),
        ("DEF", re.compile(r"\bmeans\b|\bdefined as\b|\bdefinition\b", re.IGNORECASE)),
        ("LAW", re.compile(r"\bact\b|\bregulation\b|\bstatute\b", re.IGNORECASE)),
        ("SCG", re.compile(r"\bcountr(y|ies)\b|\bjurisdiction\b|\bregion\b", re.IGNORECASE)),
        ("SCD", re.compile(r"\bdepartment\b|\bfunction\b", re.IGNORECASE)),
        ("SCE", re.compile(r"\bemployee[s]?\b|\bstaff\b", re.IGNORECASE)),
        ("ENF", re.compile(r"\bdisciplinary\b|\benforc(e|ement)\b|\bviolation\b", re.IGNORECASE)),
        ("TRN", re.compile(r"\btraining\b", re.IGNORECASE)),
        ("FLG", re.compile(r"\bred flag\b|\bwarning sign\b", re.IGNORECASE)),
        ("DND", re.compile(r"\bdo not\b|\bdon't\b|\bnever\b", re.IGNORECASE)),
        ("PUR", re.compile(r"\bpurpose\b|\bthis policy (sets out|aims|exists)\b", re.IGNORECASE)),
        ("FAQ", re.compile(r"\?\s*$")),
        ("RUL", re.compile(r"\bshall\b|\bmust\b|\bmay not\b|\bprohibited\b", re.IGNORECASE)),
    ]

    def classify(self, text: str) -> ClassificationResult:
        for label, pattern in self._PATTERNS:
            if pattern.search(text):
                return ClassificationResult(
                    label=label,
                    confidence=0.55,  # deliberately capped -- this is a heuristic fixture, never "confident"
                    rationale=f"rule-based fallback matched pattern for {label}",
                    model_id="rule_based_fallback_v1",
                    prompt_version=None,
                )
        return ClassificationResult(
            label="EXG",
            confidence=0.3,
            rationale="no rule-based pattern matched; defaulted to EXG at low confidence",
            model_id="rule_based_fallback_v1",
            prompt_version=None,
        )


def get_default_classifier() -> LETClassifier:
    """Factory used by cascade.py and the scripts. Picks Azure if credentials
    are present, otherwise falls back to the rule-based fixture with a loud
    warning -- so nobody accidentally ships fallback-classified data thinking
    it came from gpt-4o.
    """
    if os.environ.get("AZURE_OPENAI_API_KEY"):
        return AzureLETClassifier()
    import warnings

    warnings.warn(
        "AZURE_OPENAI_API_KEY is not set -- using RuleBasedFallbackClassifier. "
        "This is a plumbing fixture, not a real classifier. Do not use its output "
        "for anything shown to Claudia or fed into the evaluator as a real result.",
        stacklevel=2,
    )
    return RuleBasedFallbackClassifier()
