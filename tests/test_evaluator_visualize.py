"""evaluator/visualize.py had 0% pytest coverage before this audit --
verified only by visually opening generated PNGs during development,
never asserted on by an automated test. That's how BUG-04 (title/label
overlap) went unnoticed until manual inspection; these tests at least
lock in the DATA correctness (right number of bars/cells/points), even
though layout quality still needs a human look.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from polaris.evaluator.metrics import PredictedRow, evaluate
from polaris.evaluator.gold_loader import GoldRow
from polaris.evaluator.visualize import (
    plot_confidence_calibration, plot_confusion_matrix, plot_per_label_f1, save_all_evaluator_charts,
)


def _make_report():
    rows = [
        PredictedRow(gold=GoldRow(text="a", true_label="RUL", ambiguous=False, source_row={}), predicted_label="RUL", predicted_confidence=0.9),
        PredictedRow(gold=GoldRow(text="b", true_label="RUL", ambiguous=False, source_row={}), predicted_label="DEF", predicted_confidence=0.6),
        PredictedRow(gold=GoldRow(text="c", true_label="DEF", ambiguous=False, source_row={}), predicted_label="DEF", predicted_confidence=0.95),
    ]
    return evaluate(rows)


def _capture_figure(monkeypatch):
    captured = []
    original_close = plt.close

    def capturing_close(fig=None):
        captured.append(plt.gcf() if fig is None else fig)
        original_close(fig)

    monkeypatch.setattr(plt, "close", capturing_close)
    return captured


def test_plot_confusion_matrix_writes_file_and_has_correct_dimensions(tmp_path, monkeypatch):
    captured = _capture_figure(monkeypatch)
    report = _make_report()
    out = plot_confusion_matrix(report, tmp_path / "cm.png")
    assert out.exists() and out.stat().st_size > 0
    ax = captured[0].axes[0]
    n_labels = len(report.confusion)
    assert len(ax.get_xticklabels()) == n_labels
    assert len(ax.get_yticklabels()) == n_labels


def test_plot_confusion_matrix_raises_on_empty_report():
    class Empty:
        confusion = {}
    with pytest.raises(ValueError, match="no confusion-matrix data"):
        plot_confusion_matrix(Empty(), "/tmp/irrelevant.png")


def test_plot_per_label_f1_writes_file_with_correct_bar_count(tmp_path, monkeypatch):
    captured = _capture_figure(monkeypatch)
    report = _make_report()
    out = plot_per_label_f1(report, tmp_path / "f1.png")
    assert out.exists()
    ax = captured[0].axes[0]
    n_labels_with_f1 = sum(1 for v in report.per_label.values() if isinstance(v, dict) and "f1-score" in v)
    assert len(ax.patches) == n_labels_with_f1


def test_plot_confidence_calibration_writes_file(tmp_path):
    report = _make_report()
    out = plot_confidence_calibration(report, tmp_path / "cal.png")
    assert out.exists() and out.stat().st_size > 0


def test_plot_confidence_calibration_raises_when_all_buckets_empty():
    class Empty:
        calibration_buckets = [{"confidence_range": [0, 0.2], "n": 0, "accuracy": None}]
    with pytest.raises(ValueError, match="non-empty confidence buckets"):
        plot_confidence_calibration(Empty(), "/tmp/irrelevant.png")


def test_save_all_evaluator_charts_writes_all_three(tmp_path):
    report = _make_report()
    paths = save_all_evaluator_charts(report, tmp_path)
    assert len(paths) == 3
    assert all(p.exists() for p in paths)


def test_save_all_evaluator_charts_skips_calibration_gracefully_when_empty(tmp_path):
    class FakeReport:
        confusion = {"RUL": {"RUL": 1}}
        per_label = {"RUL": {"f1-score": 1.0, "support": 1}}
        macro_f1 = 1.0
        weighted_f1 = 1.0
        cohen_kappa = 1.0
        n_scored = 1
        calibration_buckets = [{"confidence_range": [0, 0.2], "n": 0, "accuracy": None}]  # all empty

    paths = save_all_evaluator_charts(FakeReport(), tmp_path)
    assert len(paths) == 2  # confusion matrix + per-label F1 only, calibration skipped


def test_plot_per_label_f1_raises_when_no_f1_data():
    class Empty:
        per_label = {}
    with pytest.raises(ValueError, match="no per-label F1 data"):
        plot_per_label_f1(Empty(), "/tmp/irrelevant.png")
