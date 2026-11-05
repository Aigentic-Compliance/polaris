import pytest
import uuid

from sqlalchemy import create_engine

from polaris.common.models import Segment
from polaris.scraper.segment_registry import SegmentRegistry


def _make_segment(label="RUL", text="Employees must not accept gifts.") -> Segment:
    return Segment(
        segment_id=str(uuid.uuid4()), source_id="src-1", text=text,
        label=label, confidence=0.8, review_status="auto_accepted", model_id="test",
        page_from=1, page_to=1, bbox=[1.0, 2.0, 3.0, 4.0],
    )


def test_save_and_get_segments_round_trip():
    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    segments = [_make_segment(), _make_segment(label="DEF", text="A gift means anything of value.")]
    registry.save_segments("src-1", segments)

    stored = registry.get_segments("src-1")
    assert len(stored) == 2
    assert {s["label"] for s in stored} == {"RUL", "DEF"}
    assert stored[0]["bbox"] == [1.0, 2.0, 3.0, 4.0]  # JSON round-trips correctly


def test_is_classified_reflects_actual_state():
    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    assert registry.is_classified("src-1") is False
    registry.save_segments("src-1", [_make_segment()])
    assert registry.is_classified("src-1") is True
    assert registry.is_classified("src-2") is False  # a different document is unaffected


def test_save_segments_with_empty_list_marks_nothing_classified():
    """A document that produced zero segments (e.g. entirely boilerplate)
    is a real, valid outcome -- but is different from 'never attempted.'
    is_classified should still say False for zero segments, since there's
    nothing to hand to the recommender either way, but this must not
    raise or behave inconsistently."""
    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    registry.save_segments("src-empty", [])
    assert registry.get_segments("src-empty") == []
    assert registry.is_classified("src-empty") is False


def test_save_segments_replaces_prior_segments_not_accumulates():
    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    registry.save_segments("src-1", [_make_segment(label="RUL")])
    assert len(registry.get_segments("src-1")) == 1

    registry.save_segments("src-1", [_make_segment(label="THR"), _make_segment(label="DEF")])
    stored = registry.get_segments("src-1")
    assert len(stored) == 2  # not 3 -- the old segment was replaced, not appended to
    assert {s["label"] for s in stored} == {"THR", "DEF"}


def test_stats_counts_correctly():
    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    registry.save_segments("src-1", [_make_segment(label="RUL"), _make_segment(label="RUL")])
    registry.save_segments("src-2", [_make_segment(label="DEF")])

    stats = registry.stats()
    assert stats["total_segments"] == 3
    assert stats["distinct_documents_classified"] == 2
    assert stats["label_counts"] == {"RUL": 2, "DEF": 1}


def test_stats_on_empty_registry():
    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    assert registry.stats() == {"total_segments": 0, "distinct_documents_classified": 0, "label_counts": {}}


def test_segments_with_null_bbox_round_trip_as_none():
    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    seg = Segment(
        segment_id=str(uuid.uuid4()), source_id="src-html", text="HTML segment, no page/bbox concept.",
        label="RUL", confidence=0.7, review_status="auto_accepted", model_id="test",
        page_from=None, page_to=None, bbox=None,
    )
    registry.save_segments("src-html", [seg])
    stored = registry.get_segments("src-html")
    assert stored[0]["bbox"] is None
    assert stored[0]["page_from"] is None


def test_get_engine_default_creates_sqlite_with_schema(tmp_path, monkeypatch):
    from polaris.scraper.segment_registry import get_engine, segments_table

    db_path = tmp_path / "test_segments.db"
    monkeypatch.setenv("POLARIS_DATABASE_URL", f"sqlite:///{db_path}")
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(segments_table.select()).fetchall()
        assert result == []


def test_save_segments_duplicate_segment_id_in_same_batch_raises_registry_error():
    """A real (if unlikely) failure mode: two segments with the same
    segment_id in one save call -- e.g. a bug elsewhere producing a
    collision -- must surface as a clear RegistryError, not a raw
    SQLAlchemy IntegrityError."""
    from polaris.common.exceptions import RegistryError

    registry = SegmentRegistry(engine=create_engine("sqlite:///:memory:"))
    duplicate_id = str(uuid.uuid4())
    seg1 = _make_segment()
    seg1.segment_id = duplicate_id
    seg2 = _make_segment(label="DEF")
    seg2.segment_id = duplicate_id  # same ID as seg1 -- a genuine primary-key collision

    with pytest.raises(RegistryError, match="Could not save segments"):
        registry.save_segments("src-collision", [seg1, seg2])
