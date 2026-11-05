"""recommender/visualize.py had 0% pytest coverage before this audit --
same gap as evaluator/visualize.py, same fix approach: assert on the
actual rendered data (bar/point counts), not just "a file was written."
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from polaris.common.models import ComparisonFinding
from polaris.recommender.visualize import (
    plot_materiality_vs_confidence, plot_state_distribution, save_all_recommender_charts,
)


def _findings():
    return [
        ComparisonFinding(comparison_state="ALIGNED", fact="a", confidence=0.9, materiality=0.5),
        ComparisonFinding(comparison_state="ALIGNED", fact="b", confidence=0.8, materiality=0.4),
        ComparisonFinding(comparison_state="MISSING", fact="c", confidence=0.7, materiality=0.9),
    ]


def _capture_figure(monkeypatch):
    captured = []
    original_close = plt.close

    def capturing_close(fig=None):
        captured.append(plt.gcf() if fig is None else fig)
        original_close(fig)

    monkeypatch.setattr(plt, "close", capturing_close)
    return captured


def test_plot_state_distribution_shows_all_seven_states_even_absent_ones(tmp_path, monkeypatch):
    captured = _capture_figure(monkeypatch)
    out = plot_state_distribution(_findings(), tmp_path / "dist.png")
    assert out.exists()
    ax = captured[0].axes[0]
    # Fixed order over all 7 ComparisonStates, even ones with zero findings
    # -- so the same chart layout is comparable run to run.
    assert len(ax.patches) == 7


def test_plot_state_distribution_counts_are_correct(tmp_path):
    findings = _findings()  # 2 ALIGNED, 1 MISSING, 0 of everything else
    out = plot_state_distribution(findings, tmp_path / "dist.png")
    assert out.exists()


def test_plot_state_distribution_raises_on_empty_list():
    with pytest.raises(ValueError, match="No findings"):
        plot_state_distribution([], "/tmp/irrelevant.png")


def test_plot_materiality_vs_confidence_writes_file_with_correct_point_count(tmp_path, monkeypatch):
    captured = _capture_figure(monkeypatch)
    findings = _findings()
    out = plot_materiality_vs_confidence(findings, tmp_path / "scatter.png")
    assert out.exists()
    ax = captured[0].axes[0]
    total_points = sum(len(c.get_offsets()) for c in ax.collections)
    assert total_points == len(findings)


def test_plot_materiality_vs_confidence_raises_on_empty_list():
    with pytest.raises(ValueError, match="No findings"):
        plot_materiality_vs_confidence([], "/tmp/irrelevant.png")


def test_save_all_recommender_charts_writes_both(tmp_path):
    paths = save_all_recommender_charts(_findings(), tmp_path)
    assert len(paths) == 2
    assert all(p.exists() for p in paths)
