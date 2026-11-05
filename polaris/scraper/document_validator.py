"""Stage A3 -- Document Validator (source acceptance, gate G0).

This answers exactly one question: "is this file a policy document relevant
to the target topic" -- not what is inside it paragraph by paragraph (that
is polaris.classifier, Stage B). Keeping this file's only job narrow is a
direct response to the BUAS audit's core WP1 finding (F4): a single
keyword-presence gate accepted a 176-page annual report, a 484-page
Universal Registration Document and two CSR reports as "gift policies"
because all of them contained the word "compliance" somewhere.

The checks below are still heuristic -- this is not a trained classifier --
but they combine three independent, cheap signals instead of one, and they
always produce an explicit reason string, which the BUAS validator did not.
Anything that fails or is borderline is marked for human/LLM review rather
than silently accepted.

C6.3 (WP1 framework): "Qualification proposer (classifier, LLM with
criteria, or hybrid) returning class, relevance, unit, evidence snippet
with page, and confidence -- never a bare yes/no." The deterministic rules
below handle the two confident cases cheaply and reliably (title directly
names the topic; title clearly matches a known non-policy type) and never
need an LLM call for those. The genuinely ambiguous middle case -- no
title signal, some body keywords -- used to be routed straight to
needs_review with nothing helping the human reviewer resolve it. An
optional llm_qualifier (see llm_qualifier.py) is now consulted specifically
for that case, and only that case: never to second-guess an already-
confident rule-based decision, which would spend a real API call for no
reason and make a confident outcome depend on a nondeterministic call.
"""

from __future__ import annotations

from dataclasses import dataclass

from polaris.common.exceptions import QualificationError
from polaris.common.injection_guard import detect_injection_attempt
from polaris.common.logging_config import get_logger

logger = get_logger(__name__)

POLICY_TITLE_HINTS = {
    "GFT": ["gift", "gifts and hospitality", "anti-bribery", "anti-corruption", "hospitality policy"],
}
# Documents whose title/first-page strongly suggests they are NOT a topic
# policy even if the keyword appears somewhere inside (this is the specific
# false-accept pattern from the audit -- annual reports, URDs, CSR reports).
NEGATIVE_DOCUMENT_HINTS = [
    "annual report", "universal registration document", "form 10-k",
    "sustainability report", "csr report", "integrated report",
    "proxy statement", "esg report",
]

MAX_REASONABLE_PAGE_COUNT = 40  # a topic policy this long is unusual; not a hard reject, a downweight signal

# Which D1-D11 classes lean accept vs. reject, per the framework's own
# "default action" column (document_class.py). D11 (superseded/draft)
# leans reject for the CURRENT population specifically, per the W8
# decision -- old versions are excluded from the current set, not held
# ambiguous.
ACCEPT_LEANING_CLASSES = {"D1", "D2", "D3", "D4", "D5", "D6"}
REJECT_LEANING_CLASSES = {"D7", "D8", "D9", "D10", "D11"}

# UNCALIBRATED, same honesty pattern as cascade.py's REVIEW_THRESHOLD --
# a reasonable starting point, not a measured value. See README section on
# threshold calibration: this should be tuned against real gold document-
# level data (C6.5's labelled qualification set) once it exists, not
# trusted as-is.
LLM_QUALIFIER_CONFIDENCE_THRESHOLD = 0.75


@dataclass
class ValidationResult:
    accepted: bool
    reason: str
    confidence: float  # heuristic confidence in the accept/reject decision itself
    needs_review: bool
    document_class: str | None = None  # populated only when the LLM qualifier was actually consulted


def validate_document(
    *,
    title_or_first_page_text: str,
    full_text_sample: str,
    page_count: int | None,
    topic: str = "GFT",
    keywords: list[str] | None = None,
    llm_qualifier=None,
) -> ValidationResult:
    """keywords, when given, overrides POLICY_TITLE_HINTS.get(topic) --
    this is what lets a free-text search phrase ("data privacy policy",
    typed by a person, not a pre-registered topic code) drive the same
    accept/reject logic as a known topic like GFT, without needing a new
    entry in POLICY_TITLE_HINTS for every phrase anyone might ever type.
    `topic` is still used for labelling (the topic_candidate field, log
    messages) even when `keywords` overrides the lookup.

    llm_qualifier: an optional LLMQualifier (see llm_qualifier.py),
    consulted ONLY for the genuinely ambiguous case below (no title
    signal, some body keywords). Defaults to None -- fully backward
    compatible; passing nothing behaves exactly as before this parameter
    existed. A qualification failure (QualificationError) is caught and
    logged, falling back to the pre-existing ambiguous-case behaviour
    unchanged -- this integration can only ever add information to a
    reviewer, never remove or block the existing safe default.
    """
    hint_words = keywords if keywords is not None else POLICY_TITLE_HINTS.get(topic, [])

    title_lower = title_or_first_page_text.lower()
    text_lower = full_text_sample.lower()

    negative_hit = next((neg for neg in NEGATIVE_DOCUMENT_HINTS if neg in title_lower), None)
    title_hit = next((h for h in hint_words if h.lower() in title_lower), None)
    body_hits = [h for h in hint_words if h.lower() in text_lower]

    # Signal 1: negative title hint is a strong reject, regardless of body keywords.
    # This is the exact case the BUAS keyword gate got wrong.
    if negative_hit and not title_hit:
        return ValidationResult(
            accepted=False,
            reason=f"Title/first page matches known non-policy document type ('{negative_hit}') "
                   f"without a matching topic title hint; likely a broader corporate report, not a "
                   f"{topic} policy.",
            confidence=0.85,
            needs_review=False,
        )

    # Signal 2: title itself names the topic -- strong accept.
    if title_hit:
        return ValidationResult(
            accepted=True,
            reason=f"Title/first page directly names the topic ('{title_hit}').",
            confidence=0.9,
            needs_review=False,
        )

    # Signal 3: no title signal either way -- fall back to body keyword density,
    # but treat page count as a downweight rather than a hard filter, and
    # never auto-accept on this signal alone -- route to review, UNLESS an
    # LLM qualifier is available to offer a confident second opinion.
    if body_hits:
        length_penalty = 0.0
        if page_count is not None and page_count > MAX_REASONABLE_PAGE_COUNT:
            length_penalty = 0.3
        base_confidence = min(0.3 + 0.1 * len(body_hits), 0.6) - length_penalty
        rules_reason = (
            f"No topic title match. Body contains {len(body_hits)} topic keyword(s) "
            f"({', '.join(body_hits)}) but title gives no confirming signal"
            + (f"; document is {page_count} pages, above the {MAX_REASONABLE_PAGE_COUNT}-page "
               f"heuristic for a standalone {topic} policy" if length_penalty else "")
        )

        if llm_qualifier is not None:
            injection_matches = detect_injection_attempt(title_or_first_page_text) + detect_injection_attempt(full_text_sample)
            if injection_matches:
                logger.warning(
                    "document text matched possible prompt-injection pattern(s) before LLM qualification: %s",
                    injection_matches,
                )

            try:
                qual = llm_qualifier.qualify(
                    title_or_first_page_text, full_text_sample, topic, negative_hint=negative_hit,
                )
            except QualificationError as exc:
                logger.warning("LLM qualifier failed, falling back to rules-only outcome: %s", exc)
                qual = None

            if qual is not None and injection_matches:
                # A best-effort tripwire (see injection_guard.py), not a
                # guarantee -- never silently corrected. The qualifier
                # call itself is already protected by explicit
                # untrusted-data framing, but whatever it concluded is
                # still forced to needs_review here, with the match
                # recorded, so a human decides rather than trusting a
                # confident-looking accept/reject on text that tried to
                # redirect the model.
                evidence = f" Evidence: '{qual.evidence_snippet}'." if qual.evidence_snippet else ""
                return ValidationResult(
                    accepted=False,
                    reason=f"{rules_reason}. LLM qualifier proposed class {qual.document_class} "
                           f"at confidence {qual.confidence:.2f}: {qual.rationale}{evidence} "
                           f"[FLAGGED: document text matched possible prompt-injection pattern(s): "
                           f"{', '.join(injection_matches)} -- forced to needs_review regardless of "
                           f"qualifier confidence]",
                    confidence=qual.confidence, needs_review=True, document_class=qual.document_class,
                )

            if qual is not None:
                evidence = f" Evidence: '{qual.evidence_snippet}'." if qual.evidence_snippet else ""
                if qual.relevance_level == "none":
                    return ValidationResult(
                        accepted=False,
                        reason=f"{rules_reason}. LLM qualifier found no topic relevance "
                               f"(class={qual.document_class}): {qual.rationale}{evidence}",
                        confidence=qual.confidence, needs_review=False, document_class=qual.document_class,
                    )
                if qual.document_class in ACCEPT_LEANING_CLASSES and qual.confidence >= LLM_QUALIFIER_CONFIDENCE_THRESHOLD:
                    return ValidationResult(
                        accepted=True,
                        reason=f"LLM qualifier proposed class {qual.document_class} "
                               f"({qual.relevance_level} relevance) at confidence {qual.confidence:.2f}: "
                               f"{qual.rationale}{evidence}",
                        confidence=qual.confidence, needs_review=False, document_class=qual.document_class,
                    )
                if qual.document_class in REJECT_LEANING_CLASSES and qual.confidence >= LLM_QUALIFIER_CONFIDENCE_THRESHOLD:
                    return ValidationResult(
                        accepted=False,
                        reason=f"{rules_reason}. LLM qualifier proposed class {qual.document_class} "
                               f"at confidence {qual.confidence:.2f}: {qual.rationale}{evidence}",
                        confidence=qual.confidence, needs_review=False, document_class=qual.document_class,
                    )
                # LLM was consulted but is also not confident -- still needs
                # review, but now with a proposed class and evidence attached
                # instead of nothing, which is strictly more useful to
                # whoever reviews it than the pure-rules case below.
                return ValidationResult(
                    accepted=False,
                    reason=f"{rules_reason}. LLM qualifier also uncertain (proposed class={qual.document_class}, "
                           f"confidence={qual.confidence:.2f}): {qual.rationale}{evidence} "
                           f"Routed to human review.",
                    confidence=qual.confidence, needs_review=True, document_class=qual.document_class,
                )

        # No qualifier configured, or it failed -- exact pre-existing behaviour.
        return ValidationResult(
            accepted=False,  # do not auto-accept on body keywords alone
            reason=f"{rules_reason}. Routed to human/LLM review rather than auto-rejected or auto-accepted.",
            confidence=max(base_confidence, 0.1),
            needs_review=True,
        )

    return ValidationResult(
        accepted=False,
        reason=f"No title or body signal for topic '{topic}'.",
        confidence=0.9,
        needs_review=False,
    )

# Failure modes: validate_document() is pure string/heuristic logic over
# already-extracted text for the rules path, no I/O. It never raises for
# "could not decide" -- that outcome is represented explicitly via
# needs_review=True in the return value, not via an exception, because
# "ambiguous" is an expected, common outcome here, not an error. The
# optional llm_qualifier path can raise QualificationError internally
# (network, malformed response) -- caught here and logged, never
# propagated past this function, since a qualifier failure must fall back
# to the safe pre-existing behaviour, not block document registration.
