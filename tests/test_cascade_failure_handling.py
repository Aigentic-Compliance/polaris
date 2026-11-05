import uuid

from polaris.classifier.cascade import classify_document
from polaris.classifier.llm_classifier import ClassificationResult, LETClassifier
from polaris.classifier.pdf_segmenter import RawSegment
from polaris.common.exceptions import ClassificationError


class _FlakyClassifier(LETClassifier):
    """Fails on segments containing 'FAIL_ME', succeeds otherwise -- lets a
    test control exactly which segment fails without needing a real
    Azure error."""

    def classify(self, text: str) -> ClassificationResult:
        if "FAIL_ME" in text:
            raise ClassificationError("simulated failure")
        return ClassificationResult(label="RUL", confidence=0.9, rationale="ok", model_id="test")


def test_classify_document_collects_failures_separately_not_fabricated():
    source_id = str(uuid.uuid4())
    raw_segments = [
        RawSegment(text="Employees must not accept gifts above the threshold.", page_from=1, page_to=1, bbox=(0, 0, 0, 0)),
        RawSegment(text="This one will FAIL_ME during classification.", page_from=2, page_to=2, bbox=(0, 0, 0, 0)),
        RawSegment(text="A second good segment about approval rules.", page_from=3, page_to=3, bbox=(0, 0, 0, 0)),
    ]

    result = classify_document(raw_segments, source_id=source_id, classifier=_FlakyClassifier())

    assert len(result.segments) == 2  # the two good ones
    assert len(result.failures) == 1
    assert result.failures[0].segment_index == 1
    assert "simulated failure" in result.failures[0].error
    # The failing segment must not appear anywhere in segments with a
    # guessed label standing in for it.
    assert all("FAIL_ME" not in s.text for s in result.segments)


def test_classify_document_all_succeed_has_empty_failures_list():
    source_id = str(uuid.uuid4())
    raw_segments = [
        RawSegment(text="Employees must register any gift over the threshold.", page_from=1, page_to=1, bbox=(0, 0, 0, 0)),
    ]
    result = classify_document(raw_segments, source_id=source_id, classifier=_FlakyClassifier())
    assert len(result.segments) == 1
    assert result.failures == []
