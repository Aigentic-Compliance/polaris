"""Runs the WP7 comparator against the synthetic fixtures, prints a
ranked draft report, and writes chart PNGs showing the comparison-state
distribution and materiality-vs-confidence.

Run: python scripts/run_recommender_demo.py [--chart-dir DIR]

Swap fixtures/pack_synthetic/*.json for a real PACK-GFT v1.4.0 objects.json
export and a real customer pack export -- nothing in polaris/recommender
needs to change, since it is written against the PolicyObject contract.

Failure modes: GoldDataError (a malformed fixture/pack file) and a
matplotlib drawing failure both propagate with a clear top-level message
and a non-zero exit code -- a demo that "succeeds" while quietly having
produced no charts would be worse than one that fails loudly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polaris.common.exceptions import GoldDataError
from polaris.common.logging_config import configure
from polaris.recommender.comparator import compare
from polaris.recommender.pack_loader import load_objects
from polaris.recommender.report import render_text_report

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pack_synthetic"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the recommender comparator against synthetic fixtures.")
    parser.add_argument("--chart-dir", default=None, help="Directory to write comparison charts into.")
    args = parser.parse_args()

    try:
        benchmark_objects = load_objects(FIXTURES / "benchmark_objects_synthetic.json")
        customer_objects = load_objects(FIXTURES / "customer_objects_synthetic.json")
    except GoldDataError as exc:
        print(f"Could not load pack fixtures: {exc}", file=sys.stderr)
        return 1

    findings = compare(customer_objects, benchmark_objects)
    print(render_text_report(findings))

    if args.chart_dir:
        from polaris.recommender.visualize import save_all_recommender_charts
        paths = save_all_recommender_charts(findings, args.chart_dir)
        print(f"\nCharts written: {', '.join(str(p) for p in paths)}")

    return 0


if __name__ == "__main__":
    configure()
    sys.exit(main())
