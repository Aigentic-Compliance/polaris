import pytest
from polaris.evaluator.gold_loader import GoldRow
from polaris.evaluator.metrics import PredictedRow, evaluate


def test_perfect_agreement_gives_kappa_one():
    rows = [
        PredictedRow(gold=GoldRow(text="a", true_label="RUL", ambiguous=False, source_row={}), predicted_label="RUL", predicted_confidence=0.9),
        PredictedRow(gold=GoldRow(text="b", true_label="DEF", ambiguous=False, source_row={}), predicted_label="DEF", predicted_confidence=0.9),
        PredictedRow(gold=GoldRow(text="c", true_label="RUL", ambiguous=False, source_row={}), predicted_label="RUL", predicted_confidence=0.9),
        PredictedRow(gold=GoldRow(text="d", true_label="DEF", ambiguous=False, source_row={}), predicted_label="DEF", predicted_confidence=0.9),
    ]
    report = evaluate(rows)
    assert report.cohen_kappa == 1.0
    assert report.n_ambiguous_excluded == 0


def test_ambiguous_rows_excluded_from_scoring_but_counted():
    rows = [
        PredictedRow(gold=GoldRow(text="a", true_label="RUL", ambiguous=False, source_row={}), predicted_label="RUL", predicted_confidence=0.9),
        PredictedRow(gold=GoldRow(text="b", true_label=None, ambiguous=True, source_row={}), predicted_label="THR", predicted_confidence=0.5),
    ]
    report = evaluate(rows)
    assert report.n_total == 2
    assert report.n_scored == 1
    assert report.n_ambiguous_excluded == 1


def test_evaluate_raises_when_nothing_is_scorable():
    rows = [
        PredictedRow(gold=GoldRow(text="a", true_label=None, ambiguous=True, source_row={}), predicted_label="RUL", predicted_confidence=0.5),
    ]
    with pytest.raises(ValueError, match="No scorable rows"):
        evaluate(rows)
