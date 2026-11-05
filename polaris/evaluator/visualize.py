"""Chart generation for evaluator.metrics.EvaluationReport.

Three charts, each answering a different question a reviewer would
actually ask:
  1. Confusion matrix heatmap -- which LETs get confused for which others?
  2. Per-label F1 bar chart -- which LETs is the cascade weakest on?
  3. Confidence calibration curve -- does a 0.9-confidence prediction
     actually turn out right ~90% of the time? (This is the direct
     evidence for whether the "Confidence Calibration Model" in
     MODEL_STATUS.md is worth building.)

Failure modes: save_all_evaluator_charts() creates its output directory if
missing (Path.mkdir(parents=True, exist_ok=True) -- not a failure case,
just a convenience) and lets matplotlib's own exceptions propagate on a
genuine drawing failure (e.g. an unwritable path) rather than swallowing
them, since a chart that silently failed to save is worse than a crash: it
looks like everything worked. Each chart function validates its input has
at least one data point before plotting and raises ValueError with a clear
message if not, rather than producing a blank or misleading chart.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless -- this repo never assumes a display is available
import matplotlib.pyplot as plt

from polaris.common.logging_config import get_logger

logger = get_logger(__name__)


def plot_confusion_matrix(report, out_path: str | Path) -> Path:
    labels = list(report.confusion.keys())
    if not labels:
        raise ValueError("Report has no confusion-matrix data (n_scored may be 0) -- nothing to plot.")

    matrix = [[report.confusion[t].get(p, 0) for p in labels] for t in labels]

    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 0.6), max(5, len(labels) * 0.55)))
    im = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("Predicted label")
    ax.set_ylabel("True label")
    ax.set_title(f"Confusion matrix (n_scored={report.n_scored}, kappa={report.cohen_kappa:.2f})")
    for i in range(len(labels)):
        for j in range(len(labels)):
            value = matrix[i][j]
            if value:
                ax.text(j, i, str(value), ha="center", va="center",
                        color="white" if value > (max(max(row) for row in matrix) / 2) else "black", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("wrote confusion matrix chart to %s", out_path)
    return out_path


def plot_per_label_f1(report, out_path: str | Path) -> Path:
    rows = [(label, stats["f1-score"], int(stats["support"]))
            for label, stats in report.per_label.items()
            if isinstance(stats, dict) and "f1-score" in stats]
    if not rows:
        raise ValueError("Report has no per-label F1 data -- nothing to plot.")

    rows.sort(key=lambda r: r[1])  # weakest first -- that's the actionable end of the chart
    labels, f1s, supports = zip(*rows)

    fig, ax = plt.subplots(figsize=(8, max(4, len(labels) * 0.35)))
    bars = ax.barh(labels, f1s, color=["#d62728" if f < 0.5 else "#ff7f0e" if f < 0.8 else "#2ca02c" for f in f1s])
    for bar, support in zip(bars, supports):
        ax.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2,
                f"n={support}", va="center", fontsize=7)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("F1 score")
    ax.set_title(f"Per-label F1 (macro={report.macro_f1:.2f}, weighted={report.weighted_f1:.2f})")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("wrote per-label F1 chart to %s", out_path)
    return out_path


def plot_confidence_calibration(report, out_path: str | Path) -> Path:
    buckets = [b for b in report.calibration_buckets if b["n"] > 0]
    if not buckets:
        raise ValueError("Report has no non-empty confidence buckets -- nothing to plot.")

    midpoints = [(b["confidence_range"][0] + b["confidence_range"][1]) / 2 for b in buckets]
    accuracies = [b["accuracy"] for b in buckets]
    counts = [b["n"] for b in buckets]

    fig, ax = plt.subplots(figsize=(6.5, 7))
    ax.plot([0, 1], [0, 1], linestyle="--", color="gray", label="perfect calibration")
    ax.scatter(midpoints, accuracies, s=[c * 20 for c in counts], color="#1f77b4", zorder=3)
    for x, y, n in zip(midpoints, accuracies, counts):
        ax.annotate(f"n={n}", (x, y), textcoords="offset points", xytext=(6, 4), fontsize=7)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.08)  # headroom so a point at accuracy=1.0 doesn't collide with the title
    ax.set_xlabel("Predicted confidence (bucket midpoint)")
    ax.set_ylabel("Actual accuracy in bucket")
    ax.set_title("Confidence calibration\n(points on the dashed line are well-calibrated)", fontsize=11)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("wrote confidence calibration chart to %s", out_path)
    return out_path


def save_all_evaluator_charts(report, out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    paths = [
        plot_confusion_matrix(report, out_dir / "confusion_matrix.png"),
        plot_per_label_f1(report, out_dir / "per_label_f1.png"),
    ]
    try:
        paths.append(plot_confidence_calibration(report, out_dir / "confidence_calibration.png"))
    except ValueError as exc:
        # A tiny gold set can legitimately produce empty calibration
        # buckets -- this is not an error worth failing the whole chart
        # batch over, but it must be visible, not silent.
        logger.warning("skipped confidence calibration chart: %s", exc)
    return paths
