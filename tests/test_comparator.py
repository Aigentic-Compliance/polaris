from polaris.common.models import PolicyObject
from polaris.recommender.comparator import compare
from polaris.recommender.comparison_states import ComparisonState


def _obj(object_id, label, concept_id, concept_state="mapped", parameters=None, origin="customer"):
    return PolicyObject(
        object_id=object_id, source_id="11111111-1111-1111-1111-111111111111",
        segment_id="22222222-2222-2222-2222-222222222222", source_ref="clause 1",
        label=label, concept_id=concept_id, concept_state=concept_state,
        parameters=parameters or {}, pack_origin=origin,
    )


def test_missing_when_only_in_benchmark():
    bench = [_obj("THR-B-1", "THR", "RRL-1", parameters={"threshold_value": 50}, origin="benchmark")]
    cust = []
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.MISSING.value


def test_extra_when_only_in_customer():
    bench = []
    cust = [_obj("THR-C-1", "THR", "RRL-1", parameters={"threshold_value": 50})]
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.EXTRA.value


def test_weaker_when_customer_threshold_higher():
    bench = [_obj("THR-B-1", "THR", "RRL-1", parameters={"threshold_value": 50}, origin="benchmark")]
    cust = [_obj("THR-C-1", "THR", "RRL-1", parameters={"threshold_value": 500})]
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.WEAKER.value


def test_stronger_when_customer_threshold_lower():
    bench = [_obj("THR-B-1", "THR", "RRL-1", parameters={"threshold_value": 50}, origin="benchmark")]
    cust = [_obj("THR-C-1", "THR", "RRL-1", parameters={"threshold_value": 10})]
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.STRONGER.value


def test_aligned_when_within_tolerance():
    bench = [_obj("THR-B-1", "THR", "RRL-1", parameters={"threshold_value": 50}, origin="benchmark")]
    cust = [_obj("THR-C-1", "THR", "RRL-1", parameters={"threshold_value": 51})]
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.ALIGNED.value


def test_unmapped_customer_object_is_not_comparable():
    cust = [_obj("RUL-C-1", "RUL", None, concept_state="deferred")]
    findings = compare(cust, [])
    assert findings[0].comparison_state == ComparisonState.NOT_COMPARABLE.value


def test_boolean_parameters_not_coerced_to_numeric():
    """Regression test for a real bug found in audit: bool is a Python
    subtype of int, so isinstance(True, (int, float)) is True. Without an
    explicit exclusion, a boolean parameter like requires_written_approval
    got silently treated as a numeric threshold (True/False -> 1.0/0.0),
    producing a fact string quoting 0.0/1.0 instead of the actual
    True/False values a reviewer wrote."""
    bench = [_obj("OPW-B-1", "OPW", "RRL-1", parameters={"requires_written_approval": True}, origin="benchmark")]
    cust = [_obj("OPW-C-1", "OPW", "RRL-1", parameters={"requires_written_approval": False})]
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.DIFFERENT.value
    assert "True" in findings[0].fact or "False" in findings[0].fact
    assert "1.0" not in findings[0].fact and "0.0" not in findings[0].fact


def test_qualitative_aligned_when_non_numeric_parameters_identical():
    bench = [_obj("DEF-B-1", "DEF", "DRL-1", parameters={"defines": "Government Official"}, origin="benchmark")]
    cust = [_obj("DEF-C-1", "DEF", "DRL-1", parameters={"defines": "Government Official"})]
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.ALIGNED.value


def test_not_comparable_when_numeric_but_no_directionality_rule_configured():
    """OPR/registration_window_days has no STRICTER_IS_LOWER entry -- the
    comparator must say so explicitly rather than guess a direction."""
    bench = [_obj("OPR-B-1", "OPR", "RRL-1", parameters={"registration_window_days": 10}, origin="benchmark")]
    cust = [_obj("OPR-C-1", "OPR", "RRL-1", parameters={"registration_window_days": 3})]
    findings = compare(cust, bench)
    assert findings[0].comparison_state == ComparisonState.NOT_COMPARABLE.value
    assert "no directionality rule is configured" in findings[0].fact
