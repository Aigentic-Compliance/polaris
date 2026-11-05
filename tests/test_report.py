"""report.py had 0% test coverage before this audit -- exercised only
manually via scripts/run_recommender_demo.py's printed output, never
asserted on directly.
"""
from polaris.common.models import ComparisonFinding
from polaris.recommender.report import rank_findings, render_text_report, to_json_records


def _finding(state, materiality, confidence, fact="a fact"):
    return ComparisonFinding(
        comparison_state=state, fact=fact, confidence=confidence, materiality=materiality,
    )


def test_rank_findings_orders_by_materiality_times_confidence_descending():
    low = _finding("MISSING", materiality=0.2, confidence=0.5)   # 0.10
    high = _finding("WEAKER", materiality=0.9, confidence=0.9)   # 0.81
    mid = _finding("ALIGNED", materiality=0.5, confidence=0.5)   # 0.25

    ranked = rank_findings([low, high, mid])
    assert ranked == [high, mid, low]


def test_render_text_report_includes_fact_and_recommendation():
    finding = ComparisonFinding(
        comparison_state="WEAKER", fact="Customer threshold is higher than benchmark.",
        recommendation="Consider tightening the threshold.", confidence=0.8, materiality=0.7,
        evidence_object_ids=["THR-B-1"],
    )
    text = render_text_report([finding])
    assert "WEAKER" in text
    assert "Customer threshold is higher than benchmark." in text
    assert "Consider tightening the threshold." in text
    assert "THR-B-1" in text
    assert "review_state=draft" in text or "draft" in text  # explicitly never auto-approved


def test_render_text_report_omits_recommendation_line_when_none():
    finding = ComparisonFinding(comparison_state="ALIGNED", fact="Matches.", confidence=0.9, materiality=0.5)
    text = render_text_report([finding])
    assert "DRAFT RECOMMENDATION" not in text


def test_render_text_report_empty_list():
    text = render_text_report([])
    assert "0 findings" in text


def test_to_json_records_round_trips_and_is_ranked():
    low = _finding("MISSING", materiality=0.1, confidence=0.5)
    high = _finding("WEAKER", materiality=0.9, confidence=0.9)
    records = to_json_records([low, high])
    assert isinstance(records, list)
    assert records[0]["comparison_state"] == "WEAKER"  # higher materiality*confidence ranked first
    assert all("finding_id" in r for r in records)
