"""Manual-add channel for a local document -- WP1 framework C2.3.

This is the piece that was missing: pipeline.py only knows how to discover
and download documents from the web. C2.3 requires a manual-add channel
"with the same registry treatment as automated finds" -- this script is
that channel. Point it at a local PDF or HTML file and it runs the same
steps a web-discovered candidate goes through: hash, dedupe check,
document-level validation, registration, segmentation, and classification.

Usage:
    python scripts/ingest_local_document.py --file local_documents/some_policy.pdf --organisation "Acme Corp"
    python scripts/ingest_local_document.py --file local_documents/some_policy.html --organisation "Acme Corp"

    # Skip document_validator's accept/reject decision (e.g. you already
    # know this is a real Gifts policy and want segments regardless of
    # what the heuristic decides -- useful for building the labelled
    # qualification test set, C6.5):
    python scripts/ingest_local_document.py --file local_documents/some_policy.pdf --organisation "Acme Corp" --force-accept

    # Write full classified segments to a JSON file (there is no Segment
    # database table yet -- see README known gaps -- so this is currently
    # the only way to keep the output):
    python scripts/ingest_local_document.py --file local_documents/some_policy.pdf --organisation "Acme Corp" --out segments.json

What this does NOT do: write to object storage (there is no raw store yet,
per C4.1 -- see README known gaps), or persist Segments/PolicyObjects
anywhere durable (only SourceDocument has a registry table right now).
The --out file is the only durable record of classification output until
that's built. Don't lose it if you need the result later.

Content type (PDF vs HTML) is sniffed from the actual bytes, not the file
extension -- see document_dispatch.sniff_document_type -- so a
misleadingly-named file is still handled correctly.

Failure modes: a missing file, a corrupt or unrecognized document
(DocumentParsingError -- covers both PDFParsingError and HTMLParsingError),
and a classification failure (ClassificationError, collected per-segment
rather than aborting the whole document) are all handled explicitly and
reported with a non-zero exit code where appropriate -- none of them are
silently swallowed into "0 segments found."
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polaris.classifier.cascade import classify_document
from polaris.classifier.document_dispatch import extract_preview_any, segment_any
from polaris.classifier.pdf_segmenter import detect_text_extractability
from polaris.common.exceptions import DocumentParsingError, RegistryError
from polaris.common.logging_config import configure, get_logger
from polaris.common.models import SourceDocument
from polaris.scraper.document_validator import validate_document
from polaris.scraper.hashing import sha256_of_bytes
from polaris.scraper.source_registry import SourceRegistry

logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Manually ingest one local document (WP1 C2.3 manual-add channel).")
    parser.add_argument("--file", required=True, help="Path to the local PDF or HTML file.")
    parser.add_argument("--organisation", required=True, help="Organisation name for the registry record.")
    parser.add_argument("--topic", default="GFT")
    parser.add_argument("--force-accept", action="store_true",
                         help="Register and segment regardless of document_validator's decision.")
    parser.add_argument("--out", default=None, help="Optional path to write classified segments as JSON.")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    file_bytes = path.read_bytes()
    file_hash = sha256_of_bytes(file_bytes)

    registry = SourceRegistry()
    existing = registry.exists_by_hash(file_hash)
    if existing:
        print(f"Already registered as source_id={existing} (same file_hash) -- not re-ingesting.")
        return 0

    try:
        title_text, body_sample, page_count, document_type = extract_preview_any(file_bytes)
    except DocumentParsingError as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return 1

    # Scanned-document detection only applies to PDF -- there is no HTML
    # equivalent of "image-only, no extractable text."
    extractability = detect_text_extractability(file_bytes) if document_type == "pdf" else None

    validation = validate_document(
        title_or_first_page_text=title_text, full_text_sample=body_sample,
        page_count=page_count, topic=args.topic,
    )
    accepted = validation.accepted or args.force_accept
    reason = validation.reason if not args.force_accept else f"{validation.reason} (forced accept via --force-accept)"

    doc = SourceDocument(
        raw_uri=str(path.resolve()),
        file_hash=file_hash,
        retrieved_at=datetime.now(timezone.utc),
        origin_url=None,
        organisation=args.organisation,
        topic_candidate=args.topic,
        document_type=document_type,
        robots_allowed=None,  # not applicable -- this bypassed the web entirely, per C2.3
        accepted=accepted,
        acceptance_reason=reason if accepted else "n/a",
        rejection_reason=None if accepted else reason,
        byte_size=len(file_bytes),
        requires_ocr=bool(extractability and extractability["likely_scanned"]),
    )

    try:
        registry.register(doc)
    except RegistryError as exc:
        print(f"Could not register document: {exc}", file=sys.stderr)
        return 1

    print(f"source_id={doc.source_id}  document_type={document_type}  accepted={accepted}  reason={reason}")
    if extractability and extractability["likely_scanned"]:
        print(f"WARNING: pages {extractability['suspect_scanned_pages']} look scanned (no extractable text) -- "
              f"C6.7: this needs OCR, not currently built. Segmentation below will skip those pages' content.")

    if not accepted:
        print("Document was not accepted -- not segmenting. Use --force-accept to segment anyway.")
        return 0

    raw_segments = segment_any(file_bytes, doc.source_id, document_type)
    result = classify_document(raw_segments, source_id=doc.source_id)

    print(f"\n{len(result.segments)} segment(s) classified, {len(result.failures)} failure(s).")
    for seg in result.segments[:10]:
        print(f"  [{seg.label.value:5s} conf={seg.confidence:.2f} {seg.review_status:18s}] {seg.text[:80]!r}")
    if len(result.segments) > 10:
        print(f"  ... and {len(result.segments) - 10} more (use --out to see all)")
    for f in result.failures:
        print(f"  FAILED segment {f.segment_index}: {f.error}")

    if args.out:
        payload = {
            "source_id": doc.source_id,
            "organisation": args.organisation,
            "document_type": document_type,
            "segments": [json.loads(s.model_dump_json()) for s in result.segments],
            "failures": [{"segment_index": f.segment_index, "error": f.error} for f in result.failures],
        }
        Path(args.out).write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nFull output written to {args.out}")

    return 0


if __name__ == "__main__":
    configure()
    sys.exit(main())
