"""run_evaluation.py had 0% pytest coverage before this audit -- only ever
run manually from the shell. Calls main() directly (as
test_ingest_local_document.py does for the other CLI) so failures show a
real traceback.
"""
import json
import sys

from polaris.evaluator import run_evaluation


def test_cli_runs_against_synthetic_fixture_end_to_end(tmp_path):
    out_path = tmp_path / "report.json"
    exit_code = run_evaluation.main([
        "--gold", "fixtures/gold_synthetic/gold_fixture.json",
        "--out", str(out_path),
    ])
    assert exit_code == 0
    assert out_path.exists()
    report = json.loads(out_path.read_text())
    assert report["n_total"] == 15
    assert "cohen_kappa" in report


def test_cli_with_chart_dir_writes_charts(tmp_path):
    chart_dir = tmp_path / "charts"
    exit_code = run_evaluation.main([
        "--gold", "fixtures/gold_synthetic/gold_fixture.json",
        "--chart-dir", str(chart_dir),
    ])
    assert exit_code == 0
    assert (chart_dir / "confusion_matrix.png").exists()
    assert (chart_dir / "per_label_f1.png").exists()


def test_cli_missing_gold_file_propagates_clear_error():
    import pytest
    with pytest.raises(FileNotFoundError):
        run_evaluation.main(["--gold", "/no/such/file.json"])


def test_cli_reports_classification_failures_separately(monkeypatch, capsys):
    """Simulates a classifier that fails on every call, and checks the CLI
    reports it as a distinct failure count rather than crashing or hiding it."""
    from polaris.common.exceptions import ClassificationError

    class AlwaysFailsClassifier:
        def classify(self, text):
            raise ClassificationError("simulated total failure")

    monkeypatch.setattr(run_evaluation, "get_default_classifier", lambda: AlwaysFailsClassifier())
    exit_code = run_evaluation.main(["--gold", "fixtures/gold_synthetic/gold_fixture.json"])
    captured = capsys.readouterr()
    # NOL rows still succeed via the keyword pre-filter (no LLM call needed);
    # everything else fails and must be reported, not silently dropped.
    assert exit_code == 0
    assert "row(s) failed to classify" in captured.err


def test_cli_returns_error_when_every_row_fails_classification(monkeypatch, capsys):
    from polaris.common.exceptions import ClassificationError

    class AlwaysFailsEvenOnNOL:
        def classify(self, text):
            raise ClassificationError("simulated total failure")

    # Force every row through the LLM path (never the free keyword
    # pre-filter) by making prefilter always say "send to LLM," so that
    # truly nothing survives.
    monkeypatch.setattr(run_evaluation, "get_default_classifier", lambda: AlwaysFailsEvenOnNOL())
    monkeypatch.setattr(run_evaluation, "prefilter", lambda text: type("R", (), {"skip_llm": False})())

    exit_code = run_evaluation.main(["--gold", "fixtures/gold_synthetic/gold_fixture.json"])
    assert exit_code == 1
    assert "nothing to evaluate" in capsys.readouterr().err
