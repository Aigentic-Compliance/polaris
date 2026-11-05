"""The seven comparison states from POLARIS v5.0 section 12.2, verbatim in
meaning. EXTRA is the [NEW] state added in that same section -- benchmark
concept exists on the customer side but not in the current comparison set,
which should be flagged for a human to decide whether the comparison
population itself needs extending, not silently dropped.
"""

from __future__ import annotations

from enum import Enum


class ComparisonState(str, Enum):
    ALIGNED = "ALIGNED"
    STRONGER = "STRONGER"
    WEAKER = "WEAKER"
    MISSING = "MISSING"
    DIFFERENT = "DIFFERENT"
    NOT_COMPARABLE = "NOT_COMPARABLE"
    EXTRA = "EXTRA"


STATE_MEANING = {
    ComparisonState.ALIGNED: "Customer object instantiates the same concept with materially comparable parameters.",
    ComparisonState.STRONGER: "Customer rule is more restrictive / conservative than the benchmark pattern.",
    ComparisonState.WEAKER: "Customer rule is less restrictive / less complete than the benchmark pattern.",
    ComparisonState.MISSING: "Benchmark concept exists in the comparison set but is absent from the customer policy.",
    ComparisonState.DIFFERENT: "Both contain the concept but logic/parameter/definition materially differs.",
    ComparisonState.NOT_COMPARABLE: "Scope, jurisdiction, evidence or concept mapping is insufficient for a fair comparison.",
    ComparisonState.EXTRA: "Customer policy contains a concept absent from the benchmark comparison set -- flag for a human to decide whether the comparison set needs extending.",
}

# Failure modes: none. This module is a pure enum + string lookup table --
# no I/O, no external calls, nothing that can fail at runtime except a
# programmer error (an unhandled ComparisonState value), which should
# raise a KeyError immediately rather than be caught and hidden.
