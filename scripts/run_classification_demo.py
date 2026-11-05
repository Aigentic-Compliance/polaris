"""Runs the classification cascade on a handful of inline example segments
(no PDF, no network) so you can see cascade.py's behaviour without any
setup. Uses RuleBasedFallbackClassifier unless AZURE_OPENAI_API_KEY is set.

Run: python scripts/run_classification_demo.py

Failure modes: this is a demo entry point, not a library module -- it
catches ClassificationError at the top level and prints a clear message
instead of a raw traceback, then exits non-zero, rather than either
crashing uninformatively or (worse) swallowing the error and reporting
partial/misleading results as if the demo succeeded.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polaris.classifier.cascade import classify_segment
from polaris.classifier.pdf_segmenter import RawSegment
from polaris.common.exceptions import ClassificationError
from polaris.common.logging_config import configure

EXAMPLES = [
    "Table of Contents",
    "Employees must not accept any gift with a value exceeding EUR 50 from a current or prospective supplier.",
    "Gifts must be registered in the company's gift register within five business days of receipt.",
    "3.2",
    "A gift means any item, service or benefit of value offered without expectation of direct payment.",
]


def main() -> int:
    source_id = str(uuid.uuid4())
    for i, text in enumerate(EXAMPLES, start=1):
        raw = RawSegment(text=text, page_from=1, page_to=1, bbox=(0, 0, 0, 0))
        try:
            segment = classify_segment(raw, source_id=source_id)
        except ClassificationError as exc:
            print(f"[{i}] CLASSIFICATION FAILED for {text!r}: {exc}", file=sys.stderr)
            continue
        print(f"[{i}] label={segment.label.value:5s} conf={segment.confidence:.2f} "
              f"status={segment.review_status:20s} model={segment.model_id}")
        print(f"    text: {text!r}")
        print(f"    rationale: {segment.rationale}")
        print()
    return 0


if __name__ == "__main__":
    configure()
    sys.exit(main())
