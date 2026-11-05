"""Persistence for SourceDocument records.

Storage choice: SQLite by default, swappable to Postgres via DATABASE_URL,
using SQLAlchemy Core (not ORM) so the swap is a connection-string change,
not a rewrite. SQLite is the deliberately boring choice for local
development and the first bounded-corpus proof the handover asks for
(Aigentic's stack default is Supabase Postgres for the Policy Cards
product; POLARIS gets there the same way once this runs against a real
multi-company crawl, but there is no reason to stand up Postgres to
register a few hundred rows during WP1 development).

Every candidate the scraper sees -- accepted or rejected -- is written here.
That is a deliberate response to BUAS audit finding F4/A3: rejected
candidates were never persisted, so nobody could audit what the validator
threw away or measure its false-reject rate later.

Failure modes: get_engine() lets a bad DATABASE_URL or an unreachable
database raise SQLAlchemy's own exception straight through -- this is a
startup-time configuration error, not a per-record failure, and hiding it
behind a caught exception would only delay the same failure to the first
write. register() catches IntegrityError specifically (never a bare
except) -- most commonly a file_hash collision that exists_by_hash's
caller failed to check first -- logs it with the file_hash and existing
source_id, and re-raises as RegistryError so the caller can decide whether
that's a real bug (a dedupe check was skipped) or an expected race (two
runs registering the same file concurrently). Any other database error is
not caught here and propagates as-is, so an unexpected failure mode is
never silently reclassified as "this record didn't save" with no further
signal.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, MetaData, String, Table, create_engine, insert, select
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from polaris.common.exceptions import RegistryError
from polaris.common.logging_config import get_logger
from polaris.common.models import SourceDocument

logger = get_logger(__name__)

metadata = MetaData()

source_documents = Table(
    "source_documents",
    metadata,
    Column("source_id", String, primary_key=True),
    Column("raw_uri", String, nullable=False),
    Column("file_hash", String, nullable=False, index=True),
    Column("retrieved_at", DateTime, nullable=False),
    Column("origin_url", String),
    Column("organisation", String),
    Column("topic_candidate", String, nullable=False),
    Column("document_type", String, nullable=False),
    Column("robots_allowed", Boolean),
    Column("accepted", Boolean, nullable=False),
    Column("acceptance_reason", String, nullable=False),
    Column("rejection_reason", String),
    Column("source_quality_tier", String),
    Column("version_date", String),
    Column("duplicate_of", String),
    Column("byte_size", Float),
    Column("language", String),
    Column("jurisdiction", String),
    Column("requires_ocr", Boolean),
    Column("document_class", String),
)


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.environ.get("POLARIS_DATABASE_URL", "sqlite:///polaris_registry.db")
    engine = create_engine(url)
    metadata.create_all(engine)
    return engine


class SourceRegistry:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or get_engine()
        # BUG FIXED HERE: get_engine() calls metadata.create_all() itself,
        # but an engine passed in directly (the documented pattern for
        # tests -- "SourceRegistry(engine=create_engine('sqlite:///:memory:'))")
        # never went through get_engine() and so never got its schema
        # created. Every operation against an injected engine failed with
        # "no such table: source_documents" -- caught by
        # tests/test_source_registry_errors.py, not by inspection. Calling
        # create_all() here unconditionally (idempotent -- checkfirst
        # defaults to True) makes both paths behave the same way.
        metadata.create_all(self.engine)

    def exists_by_hash(self, file_hash: str) -> str | None:
        """Return the source_id of an existing record with this hash, if any."""
        with self.engine.connect() as conn:
            row = conn.execute(
                select(source_documents.c.source_id).where(source_documents.c.file_hash == file_hash)
            ).first()
            return row[0] if row else None

    def register(self, doc: SourceDocument) -> None:
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    insert(source_documents).values(
                        source_id=doc.source_id,
                        raw_uri=doc.raw_uri,
                        file_hash=doc.file_hash,
                        retrieved_at=doc.retrieved_at,
                        origin_url=doc.origin_url,
                        organisation=doc.organisation,
                        topic_candidate=doc.topic_candidate,
                        document_type=doc.document_type,
                        robots_allowed=doc.robots_allowed,
                        accepted=doc.accepted,
                        acceptance_reason=doc.acceptance_reason,
                        rejection_reason=doc.rejection_reason,
                        source_quality_tier=doc.source_quality_tier,
                        version_date=str(doc.version_date) if doc.version_date else None,
                        duplicate_of=doc.duplicate_of,
                        byte_size=doc.byte_size,
                        language=doc.language,
                        jurisdiction=doc.jurisdiction,
                        requires_ocr=doc.requires_ocr,
                        document_class=doc.document_class,
                    )
                )
        except IntegrityError as exc:
            existing = self.exists_by_hash(doc.file_hash)
            logger.error(
                "register() failed for source_id=%s file_hash=%s -- integrity error (existing "
                "source_id with same hash: %s): %s",
                doc.source_id, doc.file_hash, existing, exc,
            )
            raise RegistryError(
                f"Could not register source_id={doc.source_id}: constraint violation "
                f"(file_hash={doc.file_hash} already registered as {existing!r})."
            ) from exc

    def all_accepted(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(source_documents).where(source_documents.c.accepted == True)  # noqa: E712
            ).mappings().all()
            return [dict(r) for r in rows]

    def stats(self) -> dict:
        with self.engine.connect() as conn:
            total = conn.execute(select(source_documents)).mappings().all()
        accepted = [r for r in total if r["accepted"]]
        rejected = [r for r in total if not r["accepted"]]
        return {
            "total_seen": len(total),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "rejection_reasons": _count_reasons(rejected),
        }


def _count_reasons(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        reason = (r.get("rejection_reason") or r.get("acceptance_reason") or "unknown")[:80]
        counts[reason] = counts.get(reason, 0) + 1
    return counts
