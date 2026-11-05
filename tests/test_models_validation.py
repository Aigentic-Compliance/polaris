"""Pydantic field validators are the actual enforcement mechanism for the
WP0 contracts -- if these don't raise on bad data, the contracts are
decorative. Coverage analysis found the raise branches themselves
untested (only valid construction was tested, never the rejection path).
"""
import pytest
from pydantic import ValidationError

from polaris.common.models import ComparisonFinding, PolicyObject, Segment, SourceDocument

VALID_SOURCE_KWARGS = dict(
    raw_uri="https://example.com/a.pdf", file_hash="sha256:" + "a" * 64,
    retrieved_at="2026-01-01T00:00:00Z", topic_candidate="GFT", document_type="pdf",
    accepted=True, acceptance_reason="test",
)


def test_source_document_rejects_malformed_hash():
    with pytest.raises(ValidationError, match="file_hash"):
        SourceDocument(**{**VALID_SOURCE_KWARGS, "file_hash": "not-a-real-hash"})


def test_source_document_rejects_wrong_length_hash():
    with pytest.raises(ValidationError):
        SourceDocument(**{**VALID_SOURCE_KWARGS, "file_hash": "sha256:" + "a" * 10})


def test_source_document_rejects_invalid_document_type():
    with pytest.raises(ValidationError, match="document_type"):
        SourceDocument(**{**VALID_SOURCE_KWARGS, "document_type": "epub"})


def test_segment_rejects_invalid_review_status():
    with pytest.raises(ValidationError, match="review_status"):
        Segment(
            source_id="11111111-1111-1111-1111-111111111111", text="x", label="RUL",
            confidence=0.5, review_status="not_a_real_status", model_id="test",
        )


def test_policy_object_rejects_invalid_concept_state():
    with pytest.raises(ValidationError, match="concept_state"):
        PolicyObject(
            object_id="RUL-GFT-0001", source_id="11111111-1111-1111-1111-111111111111",
            segment_id="22222222-2222-2222-2222-222222222222", source_ref="clause 1",
            label="RUL", concept_state="not_a_real_state",
        )


def test_comparison_finding_rejects_invalid_state():
    with pytest.raises(ValidationError, match="comparison_state"):
        ComparisonFinding(comparison_state="NOT_A_REAL_STATE", fact="x", confidence=0.5, materiality=0.5)
