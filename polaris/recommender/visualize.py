"""Chart generation for a list of ComparisonFinding records (the
recommender's output).

Two charts:
  1. Comparison-state distribution -- how many findings landed in each of
     the 7 states (ALIGNED/STRONGER/WEAKER/MISSING/DIFFERENT/
     NOT_COMPARABLE/EXTRA)? A recommender that returns 90% NOT_COMPARABLE
     is telling you concept-mapping coverage is the real problem, not the
     comparison logic -- this chart is what would show that at a glance.
  2. Materiality vs. confidence scatter, coloured by state -- v5.0 section
     12.3 says rank by materiality and confidence, not frequency; this is
     the visual version of that ranking, and it's also where you'd see if
     high-materiality findings cluster at low confidence (a sign the
     comparator needs more evidence before those specific verdicts should
     be trusted).

Failure modes: same convention as evaluator/visualize.py -- creates its
output directory, lets matplotlib exceptions propagate on a real drawing
failure, and raises ValueError early (not a blank chart) if there is
nothing to plot.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from polaris.common.logging_config import get_logger
from polaris.recommender.comparison_states import ComparisonState

logger = get_logger(__name__)

_STATE_COLORS = {
    "ALIGNED": "#2ca02c",
    "STRONGER": "#1f77b4",
    "WEAKER": "#d62728",
    "MISSING": "#ff7f0e",
    "DIFFERENT": "#9467bd",
    "NOT_COMPARABLE": "#7f7f7f",
    "EXTRA": "#17becf",
}


def plot_state_distribution(findings: list, out_path: str | Path) -> Path:
    if not findings:
        raise ValueError("No findings to plot -- comparator returned an empty list.")

    counts = Counter(f.comparison_state for f in findings)
    # Fixed, meaningful order rather than whatever order Counter happens to
    # produce, so the same chart layout is comparable run to run.
    ordered_states = [s.value for s in ComparisonState]
    values = [counts.get(s, 0) for s in ordered_states]
    colors = [_STATE_COLORS[s] for s in ordered_states]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar(ordered_states, values, color=colors)
    ax.set_ylim(0, max(values) * 1.15 + 0.5)  # headroom so the top bar's count label clears the title
    for bar, value in zip(bars, values):
        if value:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.03, str(value),
                    ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("Number of findings")
    ax.set_title(f"Comparison state distribution ({len(findings)} findings)")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("wrote comparison-state distribution chart to %s", out_path)
    return out_path


def plot_materiality_vs_confidence(findings: list, out_path: str | Path) -> Path:
    if not findings:
        raise ValueError("No findings to plot -- comparator returned an empty list.")

    fig, ax = plt.subplots(figsize=(7, 6))
    for state in ComparisonState:
        subset = [f for f in findings if f.comparison_state == state.value]
        if not subset:
            continue
        ax.scatter(
            [f.confidence for f in subset], [f.materiality for f in subset],
            label=state.value, color=_STATE_COLORS[state.value], s=60, alpha=0.8, edgecolors="black", linewidths=0.3,
        )
    ax.set_xlim(0, 1.05)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("Confidence")
    ax.set_ylabel("Materiality")
    ax.set_title("Findings ranked by materiality x confidence (v5.0 section 12.3)")
    ax.legend(loc="upper left", fontsize=8, bbox_to_anchor=(1.0, 1.0))
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("wrote materiality-vs-confidence chart to %s", out_path)
    return out_path


def save_all_recommender_charts(findings: list, out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    return [
        plot_state_distribution(findings, out_dir / "comparison_state_distribution.png"),
        plot_materiality_vs_confidence(findings, out_dir / "materiality_vs_confidence.png"),
    ]
