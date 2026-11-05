"""Scripted evaluator metrics -- the WP6 rebuild of the BUAS repo's
DQR.ipynb pattern (the audit rated the notebook's metric logic REFERENCE-
worthy, but recommended rebuilding it as a scripted, versioned evaluator
rather than a notebook nobody re-runs).

Reports, per run:
  - per-label precision / recall / F1 (macro and weighted)
  - confusion matrix
  - Cohen's kappa (chance-corrected agreement -- the same statistic the
    BUAS notebook recorded at 0.449; we compute it the same way so any
    future number is directly comparable, not just superficially similar)
  - confidence calibration: does confidence actually track correctness?
  - explicit counts of ambiguous/unscored rows, never silently dropped
    from the report even though they're excluded from the accuracy figures

Deliberately does NOT produce a single "% accuracy" headline number as the
top-line result. POLARIS v5.0 section 11.2 is explicit that a single
semantic-distance/accuracy threshold is not sufficient as the sole
validator; per-class numbers plus kappa plus calibration give Claudia and
Mada a much harder number to misquote out of context than "72% accurate."
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sklearn.metrics import classification_report, cohen_kappa_score, confusion_matrix

from polaris.common.let_taxonomy import all_codes
from polaris.evaluator.gold_loader import GoldRow


@dataclass
class PredictedRow:
    gold: GoldRow
    predicted_label: str
    predicted_confidence: float


@dataclass
class EvaluationReport:
    n_total: int
    n_scored: int
    n_ambiguous_excluded: int
    per_label: dict
    macro_f1: float
    weighted_f1: float
    cohen_kappa: float
    confusion: dict  # {true_label: {predicted_label: count}}
    calibration_buckets: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_scored": self.n_scored,
            "n_ambiguous_excluded": self.n_ambiguous_excluded,
            "per_label": self.per_label,
            "macro_f1": self.macro_f1,
            "weighted_f1": self.weighted_f1,
            "cohen_kappa": self.cohen_kappa,
            "confusion": self.confusion,
            "calibration_buckets": self.calibration_buckets,
        }


def _confidence_calibration(rows: list[PredictedRow], n_buckets: int = 5) -> list[dict]:
    """Bucket predictions by confidence and report actual accuracy per bucket.
    A well-calibrated classifier's 0.9-confidence bucket should be right
    ~90% of the time -- if it isn't, the REVIEW_THRESHOLD in cascade.py is
    not doing the job it's supposed to do, and this is where you'd see it.

    This function is the actual experiment behind the "Confidence
    Calibration Model" entry in ProtoFocus_AI_Model_Architecture.docx
    section 3 -- that trained model does not exist (see MODEL_STATUS.md).
    This report is what would tell you whether it's needed: if these
    buckets already track real accuracy closely once run against gold
    data, raw LLM self-reported confidence is good enough and the model in
    the architecture doc is not justified. If they don't, that's the
    experimental evidence for building it.
    """
    buckets = [[] for _ in range(n_buckets)]
    for r in rows:
        idx = min(int(r.predicted_confidence * n_buckets), n_buckets - 1)
        buckets[idx].append(r)

    out = []
    for i, bucket_rows in enumerate(buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        if not bucket_rows:
            out.append({"confidence_range": [lo, hi], "n": 0, "accuracy": None})
            continue
        correct = sum(1 for r in bucket_rows if r.predicted_label == r.gold.true_label)
        out.append({"confidence_range": [lo, hi], "n": len(bucket_rows), "accuracy": correct / len(bucket_rows)})
    return out


def evaluate(predictions: list[PredictedRow]) -> EvaluationReport:
    n_total = len(predictions)
    scored = [p for p in predictions if not p.gold.ambiguous and p.gold.true_label is not None]
    n_ambiguous_excluded = n_total - len(scored)

    if not scored:
        raise ValueError(
            "No scorable rows (all rows ambiguous or missing true_label). "
            "Cannot compute metrics -- check the gold file / legacy mapping."
        )

    y_true = [p.gold.true_label for p in scored]
    y_pred = [p.predicted_label for p in scored]
    labels_present = sorted(set(y_true) | set(y_pred), key=lambda l: all_codes().index(l) if l in all_codes() else 99)

    report = classification_report(y_true, y_pred, labels=labels_present, output_dict=True, zero_division=0)
    kappa = cohen_kappa_score(y_true, y_pred, labels=labels_present)
    cm = confusion_matrix(y_true, y_pred, labels=labels_present)

    confusion_dict = {
        true_label: {pred_label: int(cm[i][j]) for j, pred_label in enumerate(labels_present)}
        for i, true_label in enumerate(labels_present)
    }

    return EvaluationReport(
        n_total=n_total,
        n_scored=len(scored),
        n_ambiguous_excluded=n_ambiguous_excluded,
        per_label={k: v for k, v in report.items() if k in labels_present},
        macro_f1=report["macro avg"]["f1-score"],
        weighted_f1=report["weighted avg"]["f1-score"],
        cohen_kappa=float(kappa),
        confusion=confusion_dict,
        calibration_buckets=_confidence_calibration(scored),
    )

# Failure modes: evaluate() raises ValueError explicitly when there are no
# scorable rows rather than letting sklearn fail confusingly on empty
# input. sklearn's own warnings on degenerate inputs (a single class
# present, as in a tiny test fixture) are left to surface as warnings, not
# silenced, since that is legitimate signal the sample size is too small
# to trust the numbers, not noise to hide.
