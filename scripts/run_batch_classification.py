"""Batch classification -- the "later, separate step" this conversation
asked for. Finds accepted documents in the SourceRegistry that haven't
been classified yet (per SegmentRegistry.is_classified), reads their
original bytes back from the raw store (not the network -- see
raw_store.py), segments and classifies each one, and saves the results
durably to the segments table.

This is what makes "scrape now, classify later, maybe hours or days
apart" actually work: nothing here depends on any in-memory state from
the scraping run. Run it as many times as you like -- already-classified
documents are skipped, not reprocessed and not double-billed.

Usage:
    python scripts/run_batch_classification.py
    python scripts/run_batch_classification.py --topic GFT       # only this topic
    python scripts/run_batch_classification.py --limit 10        # cap how many to process this run
    python scripts/run_batch_classification.py --reclassify      # ignore is_classified, redo everything matched

Failure modes: a single document's raw bytes failing an integrity check
(RawStoreError -- the file on disk no longer matches the hash it was
accepted under) or failing to segment (DocumentParsingError) is logged and
skipped, not allowed to abort the whole batch -- one bad document must
never block classifying the other 199. A summary at the end reports how
many succeeded, how many were skipped and why, never silently.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polaris.classifier.cascade import classify_document
from polaris.classifier.document_dispatch import segment_any
from polaris.common.exceptions import DocumentParsingError, RawStoreError
from polaris.common.logging_config import configure, get_logger
from polaris.scraper.raw_store import DEFAULT_RAW_STORE_DIR, load_raw
from polaris.scraper.segment_registry import SegmentRegistry
from polaris.scraper.source_registry import SourceRegistry

logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify accepted documents that haven't been classified yet.")
    parser.add_argument("--topic", default=None, help="Only process this topic_candidate value.")
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many documents this run.")
    parser.add_argument("--reclassify", action="store_true", help="Reprocess documents even if already classified.")
    parser.add_argument("--raw-store-dir", default=DEFAULT_RAW_STORE_DIR)
    args = parser.parse_args()

    source_registry = SourceRegistry()
    segment_registry = SegmentRegistry()

    accepted = source_registry.all_accepted()
    if args.topic:
        accepted = [d for d in accepted if d["topic_candidate"] == args.topic]
    if not args.reclassify:
        accepted = [d for d in accepted if not segment_registry.is_classified(d["source_id"])]
    if args.limit:
        accepted = accepted[: args.limit]

    print(f"{len(accepted)} accepted document(s) to classify"
          f"{' (topic=' + args.topic + ')' if args.topic else ''}.")

    classified_count = 0
    skipped_count = 0
    total_segments = 0
    total_failures = 0

    for doc in accepted:
        source_id = doc["source_id"]
        try:
            content = load_raw(doc["raw_uri"], expected_hash=doc["file_hash"])
        except RawStoreError as exc:
            logger.error("skipping %s: could not load raw bytes: %s", source_id, exc)
            skipped_count += 1
            continue

        try:
            raw_segments = segment_any(content, source_id, doc["document_type"])
        except DocumentParsingError as exc:
            logger.error("skipping %s: segmentation failed: %s", source_id, exc)
            skipped_count += 1
            continue

        result = classify_document(raw_segments, source_id=source_id)
        segment_registry.save_segments(source_id, result.segments)

        classified_count += 1
        total_segments += len(result.segments)
        total_failures += len(result.failures)
        print(f"  {source_id}: {len(result.segments)} segment(s), {len(result.failures)} failure(s) "
              f"-- {doc['organisation']}")

    print(f"\nDone: {classified_count} document(s) classified, {skipped_count} skipped, "
          f"{total_segments} total segments saved, {total_failures} segment-level classification failures.")
    stats = segment_registry.stats()
    print(f"Segment registry now holds {stats['total_segments']} segments across "
          f"{stats['distinct_documents_classified']} documents.")

    return 0


if __name__ == "__main__":
    configure()
    sys.exit(main())
