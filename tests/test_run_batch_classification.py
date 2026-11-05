"""End-to-end tests for the batch classification script -- the actual
"scrape now, classify later, as a genuinely separate step" workflow this
was built for. Runs a real scrape first (populating the registry and raw
store), then runs the batch classifier against that same state, exactly
as it would be used in practice.
"""
import sys
import warnings
from pathlib import Path

import pymupdf as fitz
import responses
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_batch_classification as batch_module  # noqa: E402

from polaris.scraper.pipeline import run_for_organisation
from polaris.scraper.segment_registry import SegmentRegistry
from polaris.scraper.source_registry import SourceRegistry

warnings.filterwarnings("ignore", category=UserWarning)


def _make_real_gift_policy_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Global Gifts and Hospitality Policy")
    page.insert_text((72, 120), "Employees must not accept gifts exceeding EUR 50 in value.")
    return doc.tobytes()


def _run_real_scrape(tmp_path, monkeypatch, url="https://example.com/policy.pdf"):
    """Populates a real SourceRegistry + raw store, exactly as
    run_for_organisation would in a real scrape -- this is the state the
    batch classifier is meant to run against later, separately."""
    import polaris.scraper.pipeline as pipeline_module
    from polaris.scraper.discovery import Candidate

    responses.add(responses.GET, "https://example.com/robots.txt", status=404)
    responses.add(responses.GET, url, body=_make_real_gift_policy_pdf(), status=200)
    monkeypatch.setattr(
        pipeline_module, "discover_for_organisation",
        lambda *a, **kw: [Candidate(url=url, discovery_method="manual_test")],
    )

    source_registry = SourceRegistry(engine=create_engine(f"sqlite:///{tmp_path}/source.db"))
    raw_dir = tmp_path / "raw_store"
    run_for_organisation("Example Corp", "https://example.com", source_registry, topic="GFT", raw_store_dir=str(raw_dir))
    return source_registry


@responses.activate
def test_batch_classification_classifies_a_previously_scraped_document(tmp_path, monkeypatch, capsys):
    source_registry = _run_real_scrape(tmp_path, monkeypatch)
    segment_registry = SegmentRegistry(engine=create_engine(f"sqlite:///{tmp_path}/segments.db"))

    monkeypatch.setattr(batch_module, "SourceRegistry", lambda: source_registry)
    monkeypatch.setattr(batch_module, "SegmentRegistry", lambda: segment_registry)
    monkeypatch.setattr(sys, "argv", ["run_batch_classification.py"])

    exit_code = batch_module.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "1 document(s) classified" in captured.out or "classified" in captured.out

    accepted = source_registry.all_accepted()
    assert len(accepted) == 1
    assert segment_registry.is_classified(accepted[0]["source_id"]) is True
    segments = segment_registry.get_segments(accepted[0]["source_id"])
    assert len(segments) > 0


@responses.activate
def test_batch_classification_skips_already_classified_by_default(tmp_path, monkeypatch, capsys):
    source_registry = _run_real_scrape(tmp_path, monkeypatch)
    segment_registry = SegmentRegistry(engine=create_engine(f"sqlite:///{tmp_path}/segments.db"))

    monkeypatch.setattr(batch_module, "SourceRegistry", lambda: source_registry)
    monkeypatch.setattr(batch_module, "SegmentRegistry", lambda: segment_registry)
    monkeypatch.setattr(sys, "argv", ["run_batch_classification.py"])

    batch_module.main()  # first run: classifies it
    captured1 = capsys.readouterr()
    assert "1 accepted document(s) to classify" in captured1.out

    batch_module.main()  # second run: should find nothing left to do
    captured2 = capsys.readouterr()
    assert "0 accepted document(s) to classify" in captured2.out


@responses.activate
def test_reclassify_flag_forces_reprocessing(tmp_path, monkeypatch, capsys):
    source_registry = _run_real_scrape(tmp_path, monkeypatch)
    segment_registry = SegmentRegistry(engine=create_engine(f"sqlite:///{tmp_path}/segments.db"))

    monkeypatch.setattr(batch_module, "SourceRegistry", lambda: source_registry)
    monkeypatch.setattr(batch_module, "SegmentRegistry", lambda: segment_registry)

    monkeypatch.setattr(sys, "argv", ["run_batch_classification.py"])
    batch_module.main()

    monkeypatch.setattr(sys, "argv", ["run_batch_classification.py", "--reclassify"])
    batch_module.main()
    captured = capsys.readouterr()
    assert "1 accepted document(s) to classify" in captured.out  # found it again despite already being classified


@responses.activate
def test_topic_filter_only_processes_matching_topic(tmp_path, monkeypatch, capsys):
    source_registry = _run_real_scrape(tmp_path, monkeypatch)
    segment_registry = SegmentRegistry(engine=create_engine(f"sqlite:///{tmp_path}/segments.db"))

    monkeypatch.setattr(batch_module, "SourceRegistry", lambda: source_registry)
    monkeypatch.setattr(batch_module, "SegmentRegistry", lambda: segment_registry)
    monkeypatch.setattr(sys, "argv", ["run_batch_classification.py", "--topic", "SOMETHING_ELSE"])

    batch_module.main()
    captured = capsys.readouterr()
    assert "0 accepted document(s) to classify" in captured.out


@responses.activate
def test_missing_raw_bytes_skipped_not_crashing_the_batch(tmp_path, monkeypatch, capsys):
    """If raw storage was somehow lost or moved after acceptance, the
    batch must skip that one document and keep going, not crash entirely."""
    source_registry = _run_real_scrape(tmp_path, monkeypatch)
    segment_registry = SegmentRegistry(engine=create_engine(f"sqlite:///{tmp_path}/segments.db"))

    # Simulate the raw store file having been deleted after acceptance.
    accepted = source_registry.all_accepted()
    import os
    os.remove(accepted[0]["raw_uri"])

    monkeypatch.setattr(batch_module, "SourceRegistry", lambda: source_registry)
    monkeypatch.setattr(batch_module, "SegmentRegistry", lambda: segment_registry)
    monkeypatch.setattr(sys, "argv", ["run_batch_classification.py"])

    exit_code = batch_module.main()
    captured = capsys.readouterr()
    assert exit_code == 0  # does not crash
    assert "1 skipped" in captured.out
    assert segment_registry.is_classified(accepted[0]["source_id"]) is False
