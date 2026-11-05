from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from polaris.common.exceptions import RegistryError
from polaris.common.models import SourceDocument
from polaris.scraper.source_registry import SourceRegistry


def _make_doc(source_id: str, file_hash: str) -> SourceDocument:
    return SourceDocument(
        source_id=source_id,
        raw_uri="https://example.com/policy.pdf",
        file_hash=file_hash,
        retrieved_at=datetime.now(timezone.utc),
        topic_candidate="GFT",
        document_type="pdf",
        accepted=True,
        acceptance_reason="test",
    )


def test_duplicate_source_id_raises_registry_error_not_raw_sqlalchemy_error():
    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    doc1 = _make_doc("11111111-1111-1111-1111-111111111111", "sha256:" + "a" * 64)
    doc2 = _make_doc("11111111-1111-1111-1111-111111111111", "sha256:" + "b" * 64)  # same source_id, different hash

    registry.register(doc1)
    with pytest.raises(RegistryError, match="constraint violation"):
        registry.register(doc2)


def test_successful_registration_then_exists_by_hash():
    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    doc = _make_doc("22222222-2222-2222-2222-222222222222", "sha256:" + "c" * 64)
    registry.register(doc)
    assert registry.exists_by_hash(doc.file_hash) == doc.source_id
    assert registry.exists_by_hash("sha256:" + "d" * 64) is None


def test_stats_counts_accepted_and_rejected_correctly():
    """stats() and _count_reasons() had gaps in pytest coverage before
    this audit -- exercised manually while generating charts, never
    asserted on directly."""
    from datetime import datetime, timezone

    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    docs = [
        _make_doc("aaaaaaaa-0000-0000-0000-000000000001", "sha256:" + "1" * 64),
        _make_doc("aaaaaaaa-0000-0000-0000-000000000002", "sha256:" + "2" * 64),
    ]
    rejected_doc = SourceDocument(
        source_id="aaaaaaaa-0000-0000-0000-000000000003",
        raw_uri="https://example.com/annual_report.pdf",
        file_hash="sha256:" + "3" * 64,
        retrieved_at=datetime.now(timezone.utc),
        topic_candidate="GFT", document_type="pdf",
        accepted=False, acceptance_reason="n/a", rejection_reason="looks like an annual report",
    )
    for d in docs:
        registry.register(d)
    registry.register(rejected_doc)

    stats = registry.stats()
    assert stats["total_seen"] == 3
    assert stats["accepted"] == 2
    assert stats["rejected"] == 1
    assert stats["rejection_reasons"]["looks like an annual report"] == 1


def test_stats_on_empty_registry():
    registry = SourceRegistry(engine=create_engine("sqlite:///:memory:"))
    stats = registry.stats()
    assert stats == {"total_seen": 0, "accepted": 0, "rejected": 0, "rejection_reasons": {}}


def test_get_engine_default_creates_sqlite_with_schema(tmp_path, monkeypatch):
    from polaris.scraper.source_registry import get_engine, source_documents

    db_path = tmp_path / "test_registry.db"
    monkeypatch.setenv("POLARIS_DATABASE_URL", f"sqlite:///{db_path}")
    engine = get_engine()
    # Schema must already exist -- get_engine() is documented to call
    # metadata.create_all() itself.
    with engine.connect() as conn:
        result = conn.execute(source_documents.select()).fetchall()
        assert result == []
