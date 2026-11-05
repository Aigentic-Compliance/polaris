"""Mapping from the BUAS repo's old 9-label taxonomy to the current 18 LETs.

If the gold file Mada sends turns out to be (or descend from) the BUAS
Data Quality Report labels, it will use this older vocabulary, not the
canonical 18 LETs. The forensic audit table (section "Label taxonomy
comparison, structural only") is reproduced here as a *candidate* mapping
-- deliberately not applied automatically anywhere in this repo.

Two of the nine legacy labels are one-to-many:
  RUL (legacy) spans RUL, THR, OPA, OPR, OPW, TRN, PUR, ENF (canonical)
  FAQ (legacy) spans FAQ, EXG, FLG, DND (canonical)

An automatic mapping for those two would be a guess dressed up as ground
truth, which is exactly what POLARIS v5.0's "never invent" principle
prohibits applying to evaluation data. gold_loader.py refuses to load a
file using legacy labels unless the caller passes force_ambiguous_mapping,
and even then it tags every ambiguous row so the evaluator report shows a
separate "ambiguous mapping" quality tier rather than silently blending it
into the real numbers. This mirrors the audit's own explicit instruction:
mapping legacy gold labels to canonical LETs was deliberately deferred to
human/Claudia judgement, not performed by the auditor.
"""

from __future__ import annotations

DIRECT_MAPPING = {
    "NOL": "NOL",
    "DEF": "DEF",
    "LAW": "LAW",
    "SCOPE_GEOGRAPHICAL": "SCG",
    "SCOPE_DEPARTMENT": "SCD",
    "SCOPE_EMPLOYEE": "SCE",
}

AMBIGUOUS_MAPPING = {
    "RUL": ["RUL", "THR", "OPA", "OPR", "OPW", "TRN", "PUR", "ENF"],
    "FAQ": ["FAQ", "EXG", "FLG", "DND"],
}

NO_CANONICAL_EQUIVALENT = {"SCOPE_INTERNAL_EXTERNAL"}

SYSTEM_STATES = {"OTHER", "FAIL"}


class AmbiguousLegacyLabelError(Exception):
    """Raised when a legacy label has no unambiguous canonical mapping and
    the caller has not explicitly opted into ambiguous handling."""


def map_legacy_label(legacy_label: str, allow_ambiguous: bool = False) -> tuple[str | None, bool]:
    """Returns (canonical_label_or_None, is_ambiguous).

    Never guesses silently: if the label is ambiguous and allow_ambiguous is
    False, raises rather than returning a plausible-looking but unjustified
    canonical label.
    """
    if legacy_label in DIRECT_MAPPING:
        return DIRECT_MAPPING[legacy_label], False

    if legacy_label in AMBIGUOUS_MAPPING:
        if not allow_ambiguous:
            raise AmbiguousLegacyLabelError(
                f"Legacy label '{legacy_label}' maps to multiple canonical LETs "
                f"({AMBIGUOUS_MAPPING[legacy_label]}) and cannot be resolved automatically. "
                f"This requires human review of the actual paragraph text -- see module docstring."
            )
        # Even with allow_ambiguous, we do not guess which of the N canonical
        # labels is correct. We return None and mark it ambiguous so the
        # caller excludes it from strict accuracy and reports it separately.
        return None, True

    if legacy_label in NO_CANONICAL_EQUIVALENT:
        return None, True

    if legacy_label in SYSTEM_STATES:
        return None, False  # not a content label at all; caller should drop these rows

    raise ValueError(f"Unknown legacy label '{legacy_label}' -- not in any known mapping table.")
