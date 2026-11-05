"""Materiality scoring: "rank findings by materiality / confidence /
applicability, not frequency alone" (POLARIS v5.0 section 12.3).

This is a heuristic, not a validated formula -- there is no existing
materiality model to inherit (WP3/WP4 don't exist yet), so this is a
reasonable starting point Claudia should sign off on or revise, not a
number to treat as settled. It combines two independent signals:

  1. LET criticality (from the taxonomy registry -- THR/OPW/OPA carry more
     operational consequence if missed than EXG/FAQ).
  2. Parameter delta magnitude -- for numeric parameters (thresholds), how
     far apart the customer and benchmark values are, normalised by the
     benchmark value. A customer threshold at EUR 45 vs a benchmark median
     of EUR 50 is a small, low-materiality gap; EUR 500 vs EUR 50 is not.

The two are averaged rather than multiplied so that a MISSING/EXTRA finding
(no parameter to compare) still gets a materiality score driven by LET
criticality alone, rather than collapsing to zero.
"""

from __future__ import annotations

from polaris.common.let_taxonomy import criticality


def parameter_delta_score(customer_value: float | None, benchmark_value: float | None) -> float:
    """Returns 0.0 (no material difference) to 1.0 (very large difference),
    normalised by the benchmark value. Returns 0.0 if either value is missing
    (nothing to compare -- caller should rely on LET criticality alone).
    """
    if customer_value is None or benchmark_value is None or benchmark_value == 0:
        return 0.0
    delta = abs(customer_value - benchmark_value) / abs(benchmark_value)
    return min(delta, 1.0)


def materiality_score(let_code: str, customer_value: float | None = None, benchmark_value: float | None = None) -> float:
    base = criticality(let_code)
    delta = parameter_delta_score(customer_value, benchmark_value)
    if customer_value is None or benchmark_value is None:
        return round(base, 3)
    return round((base + delta) / 2, 3)

# Failure modes: none beyond normal Python errors. Pure arithmetic on
# floats/None -- no I/O, no external state. A caller passing a non-numeric
# type gets a normal TypeError rather than a caught-and-hidden one.
