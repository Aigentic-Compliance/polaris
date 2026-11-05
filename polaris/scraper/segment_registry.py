"""Durable storage for classified Segments -- the piece that makes a
genuinely separate, later batch-classification step possible at all.

Before this existed, classification results lived only in memory
(cascade.classify_document's return value), attached to one function
call's return object. Fine when classification happened inline during
scraping; not fine once classification becomes its own later step, run
separately, possibly re-run, possibly resumed after a crash -- there has
to be somewhere real for the results to go, and somewhere real to check
"has this document already been classified" without re-running (and
re-paying for) it.

This mirrors source_registry.py's shape deliberately: SQLAlchemy Core (not
ORM), SQLite by default via the same POLARIS_DATABASE_URL env var,
append-only in spirit (a document is classified once; re-classifying
replaces its prior segments in one transaction rather than accumulating
duplicates).

Failure modes: same conventions as SourceRegistry -- IntegrityError caught
narrowly and re-raised as RegistryError with context; anything else
propagates unchanged rather than being reclassified as "this didn't save."
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, DateTime, Float, MetaData, String, Table, create_engine, delete, insert, select
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from polaris.common.exceptions import RegistryError
from polaris.common.logging_config import get_logger
from polaris.common.models import Segment

logger = get_logger(__name__)

metadata = MetaData()

segments_table = Table(
    "segments",
    metadata,
    Column("segment_id", String, primary_key=True),
    Column("source_id", String, nullable=False, index=True),
    Column("text", String, nullable=False),
    Column("page_from", Float),
    Column("page_to", Float),
    Column("bbox", String),  # JSON-encoded list[float] -- SQLite has no native array type
    Column("section_path", String),
    Column("label", String, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("review_status", String, nullable=False),
    Column("model_id", String, nullable=False),
    Column("prompt_version", String),
    Column("rationale", String),
    Column("classified_at", DateTime, nullable=False),
)


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.environ.get("POLARIS_DATABASE_URL", "sqlite:///polaris_registry.db")
    engine = create_engine(url)
    metadata.create_all(engine)
    return engine


class SegmentRegistry:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()
        # Same fix as SourceRegistry: metadata.create_all() must run
        # unconditionally, not only on the default-engine path, or an
        # injected test engine silently has no tables.
        metadata.create_all(self.engine)

    def is_classified(self, source_id: str) -> bool:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(segments_table.c.segment_id).where(segments_table.c.source_id == source_id).limit(1)
            ).first()
            return row is not None

    def save_segments(self, source_id: str, segments: list[Segment]) -> None:
        """Replaces any prior segments for this source_id in one
        transaction -- re-classifying a document (e.g. after a prompt
        version change) must not leave both the old and new segments
        sitting in the table simultaneously."""
        try:
            with self.engine.begin() as conn:
                conn.execute(delete(segments_table).where(segments_table.c.source_id == source_id))
                if not segments:
                    return
                conn.execute(
                    insert(segments_table),
                    [
                        {
                            "segment_id": s.segment_id,
                            "source_id": source_id,
                            "text": s.text,
                            "page_from": s.page_from,
                            "page_to": s.page_to,
                            "bbox": json.dumps(s.bbox) if s.bbox is not None else None,
                            "section_path": s.section_path,
                            "label": s.label.value,
                            "confidence": s.confidence,
                            "review_status": s.review_status,
                            "model_id": s.model_id,
                            "prompt_version": s.prompt_version,
                            "rationale": s.rationale,
                            "classified_at": datetime.now(timezone.utc),
                        }
                        for s in segments
                    ],
                )
        except IntegrityError as exc:
            logger.error("save_segments failed for source_id=%s: %s", source_id, exc)
            raise RegistryError(f"Could not save segments for source_id={source_id}: {exc}") from exc

    def get_segments(self, source_id: str) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(segments_table).where(segments_table.c.source_id == source_id)
            ).mappings().all()
        result = []
        for r in rows:
            row = dict(r)
            row["bbox"] = json.loads(row["bbox"]) if row["bbox"] else None
            result.append(row)
        return result

    def stats(self) -> dict:
        with self.engine.connect() as conn:
            rows = conn.execute(select(segments_table)).mappings().all()
        distinct_docs = {r["source_id"] for r in rows}
        label_counts: dict[str, int] = {}
        for r in rows:
            label_counts[r["label"]] = label_counts.get(r["label"], 0) + 1
        return {
            "total_segments": len(rows),
            "distinct_documents_classified": len(distinct_docs),
            "label_counts": label_counts,
        }
