"""Stress test: concurrent SQLite writes to SourceRegistry from multiple
threads, simulating parallel candidate processing. Not part of normal
unit-test coverage goals -- this is here to catch race conditions or lost
writes under concurrency, which line/branch coverage cannot detect at all.
"""
import threading
from datetime import datetime, timezone

from sqlalchemy import create_engine

from polaris.common.models import SourceDocument
from polaris.scraper.source_registry import SourceRegistry


def test_concurrent_writes_from_multiple_threads_all_land(tmp_path):
    db_path = tmp_path / "concurrent_test.db"
    registry = SourceRegistry(engine=create_engine(f"sqlite:///{db_path}"))

    successes = []
    errors = []
    lock = threading.Lock()

    def worker(thread_id):
        for i in range(20):
            doc = SourceDocument(
                raw_uri=f"https://example.com/thread{thread_id}/doc{i}.pdf",
                file_hash=f"sha256:{thread_id:02d}{i:062d}",
                retrieved_at=datetime.now(timezone.utc),
                topic_candidate="GFT", document_type="pdf",
                accepted=True, acceptance_reason="test",
            )
            try:
                registry.register(doc)
                with lock:
                    successes.append((thread_id, i))
            except Exception as e:
                with lock:
                    errors.append((thread_id, i, str(e)))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(successes) == 200
    assert registry.stats()["total_seen"] == 200
