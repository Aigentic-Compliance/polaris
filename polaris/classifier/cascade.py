"""The cascade: this is "the labeller" as you described it -- one integrated
step the scraper's output flows through, not a separate service.

    keyword pre-filter --(confidently boilerplate)--> Segment(label=NOL)
                       --(else)--> LLM classifier --> Segment(label=<one of 18>)

Confidence routing (per POLARIS v5.0 Stage B4/B5: "low confidence routes to
review") happens here too: anything under REVIEW_THRESHOLD is marked
needs_review rather than auto_accepted, regardless of which stage produced
the label. This threshold is a placeholder -- 0.75 is a reasonable industry
default for a first pass, but the honest answer is it should be tuned
against your gold data once you send it (see evaluator/README section on
threshold calibration). Do not quote this number to Claudia as calibrated.

Failure modes: classify_segment() (one segment) lets ClassificationError
propagate -- a single-segment call has one caller who is in the best
position to decide what a failure means for them. classify_document()
(a whole document's worth of segments, the batch entry point) is different:
one segment's LLM call failing must never abort classification of the
other 200 segments in a 40-page policy, and it must never be silently
dropped either. So classify_document() catches ClassificationError per
segment, logs it, and returns failures in a separate list alongside the
successful Segments -- never a fabricated Segment standing in for the
failed one. A caller that ignores the failures list is choosing to ignore
them explicitly, which is a different thing from never being told.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from polaris.classifier.keyword_signals import prefilter
from polaris.classifier.llm_classifier import LETClassifier, get_default_classifier
from polaris.classifier.pdf_segmenter import RawSegment
from polaris.common.exceptions import ClassificationError
from polaris.common.injection_guard import detect_injection_attempt
from polaris.common.logging_config import get_logger
from polaris.common.models import Segment

logger = get_logger(__name__)

REVIEW_THRESHOLD = 0.75  # UNCALIBRATED -- see docstring


def classify_segment(
    raw: RawSegment,
    source_id: str,
    classifier: LETClassifier | None = None,
) -> Segment:
    classifier = classifier or get_default_classifier()

    pre = prefilter(raw.text)
    injection_matches = detect_injection_attempt(raw.text)
    if injection_matches:
        # A best-effort tripwire (see injection_guard.py), not a
        # guarantee -- never silently corrected or dropped. The segment
        # is still classified normally (the LLM call itself is already
        # protected by explicit untrusted-data framing plus a strict JSON
        # schema response format, which constrains the output regardless
        # of what the text asks for), but forced to needs_review
        # regardless of confidence, with the match recorded in rationale
        # so a human reviewer sees exactly why, same principle as every
        # other "uncertain" outcome in this repo.
        logger.warning(
            "segment for source_id=%s matched possible prompt-injection pattern(s): %s",
            source_id, injection_matches,
        )

    if pre.skip_llm:
        return Segment(
            segment_id=str(uuid.uuid4()),
            source_id=source_id,
            text=raw.text,
            page_from=raw.page_from,
            page_to=raw.page_to,
            bbox=list(raw.bbox),
            label="NOL",
            confidence=0.95,
            review_status="needs_review" if injection_matches else "auto_accepted",
            model_id="keyword_cascade_v1",
            rationale=_annotate_rationale(pre.reason, injection_matches),
        )

    result = classifier.classify(raw.text)
    review_status = "auto_accepted" if result.confidence >= REVIEW_THRESHOLD else "needs_review"
    if injection_matches:
        review_status = "needs_review"

    return Segment(
        segment_id=str(uuid.uuid4()),
        source_id=source_id,
        text=raw.text,
        page_from=raw.page_from,
        page_to=raw.page_to,
        bbox=list(raw.bbox),
        label=result.label,
        confidence=result.confidence,
        review_status=review_status,
        model_id=result.model_id,
        prompt_version=result.prompt_version,
        rationale=_annotate_rationale(result.rationale, injection_matches),
    )


def _annotate_rationale(rationale: str | None, injection_matches: list[str]) -> str | None:
    if not injection_matches:
        return rationale
    flag = f"[FLAGGED: text matched possible prompt-injection pattern(s): {', '.join(injection_matches)} -- forced to needs_review regardless of model confidence]"
    return f"{rationale} {flag}" if rationale else flag


def classify_document(
    raw_segments: list[RawSegment],
    source_id: str,
    classifier: LETClassifier | None = None,
) -> "BatchClassificationResult":
    classifier = classifier or get_default_classifier()
    segments: list[Segment] = []
    failures: list[ClassificationFailure] = []

    for index, rs in enumerate(raw_segments):
        try:
            segments.append(classify_segment(rs, source_id, classifier=classifier))
        except ClassificationError as exc:
            logger.error(
                "classification failed for segment %d/%d of source %s: %s -- excluded from "
                "results, not fabricated with a guessed label",
                index + 1, len(raw_segments), source_id, exc,
            )
            failures.append(ClassificationFailure(segment_index=index, source_id=source_id, error=str(exc)))

    return BatchClassificationResult(segments=segments, failures=failures)


@dataclass
class ClassificationFailure:
    segment_index: int
    source_id: str
    error: str


@dataclass
class BatchClassificationResult:
    segments: list[Segment] = field(default_factory=list)
    failures: list[ClassificationFailure] = field(default_factory=list)
