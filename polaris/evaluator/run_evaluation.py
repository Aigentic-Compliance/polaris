"""CLI entry point: run the classification cascade against a gold file and
print/save the evaluation report.

Usage:
    python -m polaris.evaluator.run_evaluation --gold path/to/gold.json [--taxonomy legacy] [--out report.json]

Runs today against the synthetic fixture (fixtures/gold_synthetic/) with no
Azure credentials needed (falls back to RuleBasedFallbackClassifier with a
loud warning). The moment Mada sends the real gold file, the same command
runs against it -- nothing else in this module changes.

Failure modes: configuration errors (bad --gold path, malformed JSON,
missing Azure credentials at import time) are allowed to propagate with
their original traceback -- these need fixing, not swallowing, and the
underlying exception already says what's wrong. Per-row classification
failures during the run are different: one row's LLM call failing must not
discard every other row's real result, so main() catches
ClassificationError per row, logs it, and reports failed rows as a
separate count in the output -- never silently drops them from n_total
and never fabricates a label to keep the row in the scored set.
"""

from __future__ import annotations

import argparse
import json
import sys

from polaris.classifier.keyword_signals import prefilter
from polaris.classifier.llm_classifier import get_default_classifier
from polaris.common.exceptions import ClassificationError
from polaris.common.logging_config import configure, get_logger
from polaris.evaluator.gold_loader import load_gold_file
from polaris.evaluator.metrics import PredictedRow, evaluate

logger = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the LET classification cascade against gold data.")
    parser.add_argument("--gold", required=True, help="Path to gold JSON file.")
    parser.add_argument("--taxonomy", choices=["canonical", "legacy"], default="canonical")
    parser.add_argument("--out", default=None, help="Optional path to write the JSON report.")
    parser.add_argument("--chart-dir", default=None, help="Optional directory to write PNG charts into.")
    args = parser.parse_args(argv)

    gold_rows = load_gold_file(args.gold, taxonomy=args.taxonomy)
    classifier = get_default_classifier()

    predictions: list[PredictedRow] = []
    classification_failures = 0
    for i, row in enumerate(gold_rows):
        pre = prefilter(row.text)
        if pre.skip_llm:
            predictions.append(PredictedRow(gold=row, predicted_label="NOL", predicted_confidence=0.95))
            continue
        try:
            result = classifier.classify(row.text)
        except ClassificationError as exc:
            logger.error("classification failed for gold row %d/%d: %s -- excluded from metrics, "
                         "not scored as a guess", i + 1, len(gold_rows), exc)
            classification_failures += 1
            continue
        predictions.append(PredictedRow(gold=row, predicted_label=result.label, predicted_confidence=result.confidence))

    if not predictions:
        print("No rows were successfully classified -- nothing to evaluate.", file=sys.stderr)
        return 1

    report = evaluate(predictions)
    report_dict = report.to_dict()

    print(json.dumps({
        "n_total": report.n_total,
        "n_scored": report.n_scored,
        "n_ambiguous_excluded": report.n_ambiguous_excluded,
        "n_classification_failures": classification_failures,
        "macro_f1": round(report.macro_f1, 3),
        "weighted_f1": round(report.weighted_f1, 3),
        "cohen_kappa": round(report.cohen_kappa, 3),
    }, indent=2))
    print("\nPer-label F1 (see full report for precision/recall):")
    for label, stats in report.per_label.items():
        if isinstance(stats, dict) and "f1-score" in stats:
            print(f"  {label:5s} f1={stats['f1-score']:.3f}  support={int(stats['support'])}")

    if classification_failures:
        print(f"\n{classification_failures} row(s) failed to classify and were excluded -- see logs above.",
              file=sys.stderr)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(report_dict, f, indent=2)
        print(f"\nFull report written to {args.out}")

    if args.chart_dir:
        from polaris.evaluator.visualize import save_all_evaluator_charts
        paths = save_all_evaluator_charts(report, args.chart_dir)
        print(f"\nCharts written: {', '.join(str(p) for p in paths)}")

    return 0


if __name__ == "__main__":
    configure()
    sys.exit(main())
