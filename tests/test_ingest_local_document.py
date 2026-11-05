"""Tests for scripts/ingest_local_document.py -- the C2.3 manual-add
channel. Imports main() directly rather than shelling out, so failures
show a real Python traceback instead of a swallowed subprocess exit code.
"""
import sys
from pathlib import Path

import pymupdf as fitz
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ingest_local_document as ingest  # noqa: E402


def _make_test_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Global Gifts and Hospitality Policy")
    page.insert_text((72, 120), "Employees must not accept any gift exceeding EUR 50 in value.")
    doc.save(str(path))


def test_ingest_missing_file_returns_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ingest_local_document.py", "--file", "/no/such/file.pdf", "--organisation", "Test Co"])
    exit_code = ingest.main()
    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


def test_ingest_real_pdf_produces_segments(tmp_path, monkeypatch, capsys):
    pdf_path = tmp_path / "policy.pdf"
    _make_test_pdf(pdf_path)

    # Point the registry at a throwaway in-memory DB so this test never
    # touches polaris_registry.db on disk.
    from polaris.scraper.source_registry import SourceRegistry

    test_engine = create_engine("sqlite:///:memory:")
    registry_instance = SourceRegistry(engine=test_engine)
    monkeypatch.setattr(ingest, "SourceRegistry", lambda: registry_instance)

    out_path = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", [
        "ingest_local_document.py", "--file", str(pdf_path), "--organisation", "Test Co", "--out", str(out_path),
    ])
    exit_code = ingest.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "accepted=True" in captured.out
    assert out_path.exists()

    import json
    payload = json.loads(out_path.read_text())
    assert len(payload["segments"]) == 2  # title line + the EUR 50 rule, as separate segments


def test_ingest_html_file_produces_segments(tmp_path, monkeypatch, capsys):
    """The manual-add channel must accept HTML files too, not just PDFs --
    dispatched by content, not by file extension."""
    html_path = tmp_path / "policy.html"
    html_path.write_text(
        "<html><head><title>Gifts Policy</title></head>"
        "<body><p>Employees must not accept gifts exceeding EUR 50 in value.</p></body></html>"
    )

    from polaris.scraper.source_registry import SourceRegistry
    test_engine = create_engine("sqlite:///:memory:")
    registry_instance = SourceRegistry(engine=test_engine)
    monkeypatch.setattr(ingest, "SourceRegistry", lambda: registry_instance)

    out_path = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", [
        "ingest_local_document.py", "--file", str(html_path), "--organisation", "Test Co", "--out", str(out_path),
    ])
    exit_code = ingest.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "document_type=html" in captured.out
    assert "accepted=True" in captured.out

    import json
    payload = json.loads(out_path.read_text())
    assert payload["document_type"] == "html"
    assert len(payload["segments"]) == 1
