"""Core WP7 comparator: customer PolicyObjects vs benchmark PolicyObjects,
grouped by RRL/DRL concept_id, classified into one of the seven
ComparisonStates.

Grouping key is concept_id, not text similarity -- this is the whole reason
POLARIS insists on RRL/DRL concept mapping before comparison exists at all
(v5.0 section 8.2, section 20 "benchmark laundering" risk). Two objects
that happen to embed close together are not the same concept; two objects
mapped to the same concept_id by a human-reviewed or deferred-but-confirmed
mapping step are. Anything still `unmapped` or `deferred` cannot be safely
grouped, so it is always reported as NOT_COMPARABLE rather than guessed
into a bucket by text similarity -- that guess is precisely the mechanism
the BUAS "benchmark" used (embedding clusters) and precisely what the audit
criticised.

Numeric-parameter direction (is a lower threshold "stricter" or "weaker")
is topic- and field-specific and must not be hardcoded as a universal rule
-- STRICTER_IS_LOWER below is an explicit, inspectable config dict for
exactly this reason. Add entries as new topics/parameters are introduced;
do not infer direction from the field name.
"""

from __future__ import annotations

import uuid

from polaris.common.let_taxonomy import LET_REGISTRY
from polaris.common.models import ComparisonFinding, PolicyObject
from polaris.recommender.comparison_states import ComparisonState
from polaris.recommender.materiality import materiality_score

# (LET, parameter_key) -> True if a LOWER value is the stricter/more
# conservative position. Gift-value thresholds: lower ceiling = stricter.
# Training-hour requirements would be the opposite (higher = stricter) and
# must be added explicitly, never assumed.
STRICTER_IS_LOWER: dict[tuple[str, str], bool] = {
    ("THR", "threshold_value"): True,
}

RELATIVE_TOLERANCE = 0.05  # within 5% of each other counts as ALIGNED, not STRONGER/WEAKER


def _group_by_concept(objects: list[PolicyObject]) -> dict[str, list[PolicyObject]]:
    groups: dict[str, list[PolicyObject]] = {}
    for obj in objects:
        if obj.concept_state != "mapped" or not obj.concept_id:
            continue  # unmapped/deferred handled separately as NOT_COMPARABLE
        groups.setdefault(obj.concept_id, []).append(obj)
    return groups


def _compare_pair(customer_obj: PolicyObject, benchmark_obj: PolicyObject) -> tuple[ComparisonState, str, str | None]:
    """Returns (state, fact, recommendation_or_None)."""
    let_name = LET_REGISTRY.get(customer_obj.label.value, None)
    let_label = let_name.name if let_name else customer_obj.label.value

    # bool is deliberately excluded here even though isinstance(True, int)
    # is True in Python -- a real bug found during audit: a boolean
    # parameter like {"requires_written_approval": True} was silently
    # coerced into 1.0/0.0 and compared as a numeric threshold, producing
    # a fact string that read "customer requires_written_approval=0.0
    # differs from benchmark 1.0" instead of the true/false values a
    # reviewer actually wrote. Booleans are qualitative, not points on a
    # numeric scale, and must go through the equality-comparison path
    # below, not the numeric-threshold path.
    shared_numeric_keys = [
        k for k in customer_obj.parameters
        if k in benchmark_obj.parameters
        and isinstance(customer_obj.parameters[k], (int, float)) and not isinstance(customer_obj.parameters[k], bool)
        and isinstance(benchmark_obj.parameters[k], (int, float)) and not isinstance(benchmark_obj.parameters[k], bool)
    ]

    if not shared_numeric_keys:
        # No comparable numeric parameter -- compare qualitatively.
        # This plain-equality check is the current stand-in for the
        # "Policron Semantic Equivalence Model" described in
        # ProtoFocus_AI_Model_Architecture.docx section 3 -- that model is
        # not built (see MODEL_STATUS.md). Every DIFFERENT verdict produced
        # here on a non-numeric mismatch is itself evidence toward whether
        # that model is actually needed: if plain equality keeps producing
        # false DIFFERENT verdicts on clauses a human would call equivalent,
        # that's the experimental justification: dumb comparison; the data
        # this produces is the argument for or against building it.
        if customer_obj.parameters == benchmark_obj.parameters:
            return (
                ComparisonState.ALIGNED,
                f"Customer {let_label} object matches the benchmark concept with identical parameters.",
                None,
            )
        return (
            ComparisonState.DIFFERENT,
            f"Customer {let_label} object instantiates the same concept but with different parameters "
            f"({customer_obj.parameters} vs benchmark {benchmark_obj.parameters}).",
            "Review whether the parameter difference is intentional or should be reconciled.",
        )

    key = shared_numeric_keys[0]  # first shared numeric key drives the comparison
    c_val, b_val = float(customer_obj.parameters[key]), float(benchmark_obj.parameters[key])
    rel_diff = abs(c_val - b_val) / abs(b_val) if b_val else float("inf")

    if rel_diff <= RELATIVE_TOLERANCE:
        return (
            ComparisonState.ALIGNED,
            f"Customer {key}={c_val} is within {RELATIVE_TOLERANCE*100:.0f}% of the benchmark value {b_val}.",
            None,
        )

    stricter_is_lower = STRICTER_IS_LOWER.get((customer_obj.label.value, key))
    if stricter_is_lower is None:
        return (
            ComparisonState.NOT_COMPARABLE,
            f"Customer {key}={c_val} differs from benchmark {b_val}, but no directionality rule is "
            f"configured for ({customer_obj.label.value}, {key}) -- cannot judge stricter/weaker safely.",
            "Add a STRICTER_IS_LOWER entry for this (LET, parameter) pair once direction is confirmed.",
        )

    customer_is_stricter = (c_val < b_val) if stricter_is_lower else (c_val > b_val)
    if customer_is_stricter:
        return (
            ComparisonState.STRONGER,
            f"Customer {key}={c_val} is more restrictive than the benchmark value {b_val}.",
            None,
        )
    return (
        ComparisonState.WEAKER,
        f"Customer {key}={c_val} is less restrictive than the benchmark value {b_val}.",
        f"Consider whether {key} should be tightened towards the benchmark value {b_val}.",
    )


def compare(customer_objects: list[PolicyObject], benchmark_objects: list[PolicyObject]) -> list[ComparisonFinding]:
    customer_groups = _group_by_concept(customer_objects)
    benchmark_groups = _group_by_concept(benchmark_objects)

    all_concepts = set(customer_groups) | set(benchmark_groups)
    findings: list[ComparisonFinding] = []

    for concept_id in sorted(all_concepts):
        c_objs = customer_groups.get(concept_id, [])
        b_objs = benchmark_groups.get(concept_id, [])

        if c_objs and b_objs:
            c_obj, b_obj = c_objs[0], b_objs[0]  # first-of-group; multi-object concepts are a known simplification, see README
            state, fact, recommendation = _compare_pair(c_obj, b_obj)
            numeric_keys = [k for k in c_obj.parameters if k in b_obj.parameters and isinstance(c_obj.parameters[k], (int, float))]
            c_val = c_obj.parameters.get(numeric_keys[0]) if numeric_keys else None
            b_val = b_obj.parameters.get(numeric_keys[0]) if numeric_keys else None
            findings.append(ComparisonFinding(
                comparison_state=state.value,
                customer_object_id=c_obj.object_id,
                benchmark_concept_id=concept_id,
                fact=fact,
                recommendation=recommendation,
                evidence_object_ids=[o.object_id for o in b_objs],
                confidence=0.8 if state != ComparisonState.NOT_COMPARABLE else 0.3,
                materiality=materiality_score(c_obj.label.value, c_val, b_val),
            ))
        elif b_objs and not c_objs:
            b_obj = b_objs[0]
            findings.append(ComparisonFinding(
                comparison_state=ComparisonState.MISSING.value,
                customer_object_id=None,
                benchmark_concept_id=concept_id,
                fact=f"Benchmark concept {concept_id} ({b_obj.label.value}) is not instantiated by any object "
                     f"in the customer pack.",
                recommendation="Consider whether the customer policy should address this concept.",
                evidence_object_ids=[o.object_id for o in b_objs],
                confidence=0.8,
                materiality=materiality_score(b_obj.label.value),
            ))
        elif c_objs and not b_objs:
            c_obj = c_objs[0]
            findings.append(ComparisonFinding(
                comparison_state=ComparisonState.EXTRA.value,
                customer_object_id=c_obj.object_id,
                benchmark_concept_id=None,
                fact=f"Customer object {c_obj.object_id} maps to concept {concept_id}, which is not present "
                     f"in the current benchmark comparison set.",
                recommendation="Flag for human review: should this concept be added to the comparison set?",
                evidence_object_ids=[],
                confidence=0.6,
                materiality=materiality_score(c_obj.label.value),
            ))

    # Anything unmapped/deferred on the customer side is reported as
    # NOT_COMPARABLE rather than silently dropped -- it exists, it just
    # can't be grouped safely.
    for obj in customer_objects:
        if obj.concept_state != "mapped":
            findings.append(ComparisonFinding(
                comparison_state=ComparisonState.NOT_COMPARABLE.value,
                customer_object_id=obj.object_id,
                benchmark_concept_id=None,
                fact=f"Customer object {obj.object_id} ({obj.label.value}) is {obj.concept_state}, not mapped "
                     f"to a reference concept, and cannot be compared.",
                recommendation=None,
                evidence_object_ids=[],
                confidence=0.2,
                materiality=materiality_score(obj.label.value),
            ))

    return findings

# Failure modes: comparator.compare() and _compare_pair() do no I/O and
# call no external services -- every input is already-validated
# PolicyObject data. The one deliberate non-fatal path is the
# STRICTER_IS_LOWER lookup miss in _compare_pair(), which returns
# NOT_COMPARABLE with an explanatory fact string rather than raising or
# guessing a direction -- see the comment at STRICTER_IS_LOWER above.
# Malformed PolicyObject data cannot reach this module: pack_loader.py and
# the pydantic model already reject it before comparator.py ever runs.
