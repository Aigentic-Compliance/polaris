"""The canonical 18 Library Entity Types (LETs).

Source: POLARIS v5.0 section 7.3. This is the single place the taxonomy is
defined -- every other module imports LET from here rather than redeclaring
the list, so there is exactly one file to touch if Claudia revises the
taxonomy.

Design note on NOL: NOL ("No Label") is not a separate accept/reject flag.
It is a normal member of this enum. Document-level acceptance (is this file
a policy at all -- POLARIS Stage A / gate G0) and segment-level labelling
(what kind of content is this passage -- Stage B) are two different
decisions in the architecture, but you do not need two taxonomies to keep
them separate: NOL is simply the classifier's answer for boilerplate,
navigation, headers/footers and non-substantive text at the segment level.
Keep the two decisions as two fields (SourceDocument.accepted vs
Segment.label == NOL) rather than merging them into one signal -- that
merge is exactly how the BUAS scraper's single keyword gate ended up
accepting a 176-page annual report as a gift policy (see forensic audit
finding F4).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LET(str, Enum):
    NOL = "NOL"
    PUR = "PUR"
    RUL = "RUL"
    THR = "THR"
    OPA = "OPA"
    OPR = "OPR"
    OPW = "OPW"
    SCG = "SCG"
    SCD = "SCD"
    SCE = "SCE"
    DEF = "DEF"
    LAW = "LAW"
    FAQ = "FAQ"
    EXG = "EXG"
    FLG = "FLG"
    DND = "DND"
    ENF = "ENF"
    TRN = "TRN"


@dataclass(frozen=True)
class LETSpec:
    code: str
    name: str
    description: str
    # Coarse severity used later by the recommender's materiality score.
    # RUL/THR/OPW carry operational consequences if missed; EXG/FAQ/DND do not.
    criticality: float


LET_REGISTRY: dict[str, LETSpec] = {
    "NOL": LETSpec("NOL", "No Label", "Non-substantive text: boilerplate, navigation, headers/footers, page furniture.", 0.0),
    "PUR": LETSpec("PUR", "Purpose", "Statement of why the policy exists / its objective.", 0.2),
    "RUL": LETSpec("RUL", "Rules -- General", "A general normative rule not captured by a more specific subtype.", 0.8),
    "THR": LETSpec("THR", "Rules -- Thresholds & Currency", "A rule expressed as a numeric threshold, currency amount or band.", 1.0),
    "OPA": LETSpec("OPA", "Operational -- Approval", "A rule requiring prior approval from a named role/function.", 0.9),
    "OPR": LETSpec("OPR", "Operational -- Registration", "A rule requiring registration/logging of an event.", 0.7),
    "OPW": LETSpec("OPW", "Operational -- Waiver / Exception", "A rule describing an exception or waiver process.", 0.9),
    "SCG": LETSpec("SCG", "Scope -- Geographical", "Defines which countries/regions the policy or a clause applies to.", 0.5),
    "SCD": LETSpec("SCD", "Scope -- Departments", "Defines which departments/functions a clause applies to.", 0.5),
    "SCE": LETSpec("SCE", "Scope -- Employees", "Defines which employees/roles a clause applies to.", 0.5),
    "DEF": LETSpec("DEF", "Definitions", "Defines a material term used elsewhere in the policy.", 0.6),
    "LAW": LETSpec("LAW", "Laws / Regulations", "References an external law or regulation.", 0.7),
    "FAQ": LETSpec("FAQ", "Frequently Asked Questions", "A question-and-answer pair.", 0.3),
    "EXG": LETSpec("EXG", "Examples / Guidelines", "Illustrative example or non-binding guidance.", 0.2),
    "FLG": LETSpec("FLG", "Red Flags", "Warning sign / risk indicator employees should notice.", 0.6),
    "DND": LETSpec("DND", "Do's and Don'ts", "A prescriptive but informal do/don't statement.", 0.4),
    "ENF": LETSpec("ENF", "Policy Operations / Enforcement", "Consequences of non-compliance / enforcement mechanics.", 0.7),
    "TRN": LETSpec("TRN", "Employee Training Requirement", "A required training obligation.", 0.5),
}


def all_codes() -> list[str]:
    return list(LET_REGISTRY.keys())


def criticality(code: str) -> float:
    spec = LET_REGISTRY.get(code)
    return spec.criticality if spec else 0.0

# Failure modes: none. Static registry data + pure lookups (dict.get with
# a default). A missing LET code returns 0.0 criticality rather than
# raising, which is the correct behaviour here (an unregistered/future
# code should not crash a materiality calculation), not a hidden failure --
# see LET_REGISTRY for the one place codes are defined.
