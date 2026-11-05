"""Chart generation for SourceRegistry.stats().

Two charts:
  1. Accepted / rejected / needs-review breakdown -- the top-line "what
     happened to everything we found" view.
  2. Rejection-reason breakdown -- which specific reason (annual report,
     no title match, etc.) accounts for the rejects. This is the chart
     that would have made the BUAS false-accept pattern visible
     immediately, if it had existed: one reason dominating unexpectedly is
     exactly the signal that would catch a validator systematically
     misclassifying one document type.

Failure modes: same convention as the other visualize.py modules --
creates its output directory, propagates real matplotlib failures, raises
ValueError early on empty input rather than drawing a blank/misleading
chart.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from polaris.common.logging_config import get_logger

logger = get_logger(__name__)


def plot_registry_breakdown(stats: dict, out_path: str | Path) -> Path:
    if stats.get("total_seen", 0) == 0:
        raise ValueError("Registry has 0 candidates -- nothing to plot yet.")

    accepted = stats["accepted"]
    rejected = stats["rejected"]
    fig, ax = plt.subplots(figsize=(5, 5))
    labels = ["Accepted", "Rejected"]
    values = [accepted, rejected]
    colors = ["#2ca02c", "#d62728"]
    wedges, _, autotexts = ax.pie(
        values, labels=labels, colors=colors, autopct=lambda p: f"{p:.0f}%\n({int(round(p / 100 * sum(values)))})",
        startangle=90,
    )
    ax.set_title(f"Source registry outcome (n={stats['total_seen']})")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("wrote registry breakdown chart to %s", out_path)
    return out_path


def plot_rejection_reasons(stats: dict, out_path: str | Path) -> Path:
    reasons = stats.get("rejection_reasons", {})
    if not reasons:
        raise ValueError("No rejection reasons recorded -- nothing to plot.")

    sorted_reasons = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:10]  # top 10, else unreadable
    labels, counts = zip(*sorted_reasons)
    # 90/87 rather than a shorter cutoff: several real rejection reasons in
    # this codebase (document_validator.py) share a ~60-character common
    # prefix ("Title/first page matches known non-policy document type
    # ('...')"), and the part that actually distinguishes them comes right
    # after that prefix. A shorter truncation cut before that point and
    # produced two visibly identical labels for two different reasons.
    truncated_labels = [lbl if len(lbl) <= 90 else lbl[:87] + "..." for lbl in labels]

    fig, ax = plt.subplots(figsize=(9, max(3, len(labels) * 0.5)))
    # Use explicit numeric y-positions rather than letting barh treat the
    # label strings themselves as the categorical axis. Several distinct
    # rejection reasons in this codebase share a long common prefix (e.g.
    # "Title/first page matches known non-policy document type ('...')"),
    # so after truncating to a readable length, two DIFFERENT reasons can
    # produce the IDENTICAL truncated string -- and matplotlib silently
    # merges bars that share a categorical label, making one real reason
    # disappear from the chart with no error. This is exactly a silent
    # failure: the code runs, produces a plausible-looking chart, and is
    # simply wrong. Positioning by index and setting tick labels separately
    # keeps every bar visually distinct even when two labels read the same.
    y_positions = range(len(labels))
    ax.barh(list(y_positions)[::-1], counts[::-1], color="#d62728")
    ax.set_yticks(list(y_positions)[::-1])
    ax.set_yticklabels(truncated_labels)
    ax.set_xlabel("Count")
    ax.set_title("Rejection reasons (top 10)")
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("wrote rejection-reasons chart to %s", out_path)
    return out_path


def save_all_registry_charts(stats: dict, out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    paths = [plot_registry_breakdown(stats, out_dir / "registry_breakdown.png")]
    try:
        paths.append(plot_rejection_reasons(stats, out_dir / "rejection_reasons.png"))
    except ValueError as exc:
        logger.warning("skipped rejection-reasons chart: %s", exc)
    return paths
