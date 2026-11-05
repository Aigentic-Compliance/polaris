"""The D1-D11 document-class vocabulary -- DECIDED 18 September 2026 per
the WP1 Target Capability & Acceptance Framework v0.2, section on document
qualification. This is the WP1 v1.0 pilot vocabulary for Gifts: current,
not a permanently exhaustive taxonomy for future topics.

This is a genuinely different taxonomy from the 18 LETs
(polaris.common.let_taxonomy) -- LET classifies a paragraph's content
("this sentence is a threshold rule"), D1-D11 classifies a whole document's
role ("this file is a supplier code of conduct"). Keeping them as separate
enums, never conflated, matters for the same reason document-level
accept/reject and paragraph-level classification were kept as two fields
from the start of this repo (see let_taxonomy.py's docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DocumentClass(str, Enum):
    D1 = "D1"   # Dedicated topic policy
    D2 = "D2"   # Broader policy with a topic section
    D3 = "D3"   # Employee Code of Conduct with a topic section
    D4 = "D4"   # Third-party / supplier code
    D5 = "D5"   # Supporting instrument (annex, procedure, guideline)
    D6 = "D6"   # Local / country policy or addendum
    D7 = "D7"   # Reporting document mentioning the topic (annual/CSR/URD)
    D8 = "D8"   # Template or model policy (not an organisation's own rule)
    D9 = "D9"   # Commentary / news / training material
    D10 = "D10"  # Other compliance document, off-topic
    D11 = "D11"  # Superseded or draft version


@dataclass(frozen=True)
class DocumentClassSpec:
    code: str
    name: str
    example: str
    default_at_g0: str  # proposed default action, per the framework's own table -- never auto-applied, always a proposal for human/LLM review to confirm


DOCUMENT_CLASS_REGISTRY: dict[str, DocumentClassSpec] = {
    "D1": DocumentClassSpec("D1", "Dedicated topic policy", "Gifts, Entertainment & Hospitality Policy", "Accept"),
    "D2": DocumentClassSpec("D2", "Broader policy with topic section", "Anti-Bribery & Corruption Policy with gifts chapter", "Accept section (part_of)"),
    "D3": DocumentClassSpec("D3", "Employee Code of Conduct with topic section", "Code of Conduct, 'Gifts and hospitality' page", "Accept section; flag as CoC-level"),
    "D4": DocumentClassSpec("D4", "Third-party / supplier code", "Supplier Code of Conduct, Business Partner Code", "Separate class; accept only if population includes third-party rules"),
    "D5": DocumentClassSpec("D5", "Supporting instrument", "Annex, procedure, guideline, register form, local threshold table", "Accept as companion_of its parent"),
    "D6": DocumentClassSpec("D6", "Local / country policy or addendum", "Country gifts addendum", "Accept as companion_of or standalone with geographic scope"),
    "D7": DocumentClassSpec("D7", "Reporting document mentioning topic", "Annual report, sustainability/CSR report, universal registration document, 10-K narrative", "Reject as policy source"),
    "D8": DocumentClassSpec("D8", "Template or model policy", "NGO, consultancy or law-firm model policy", "Reject from organisation population"),
    "D9": DocumentClassSpec("D9", "Commentary / news / training material", "Article, blog, e-learning brochure", "Reject"),
    "D10": DocumentClassSpec("D10", "Other compliance document, off-topic", "Privacy policy, whistleblowing policy without gifts rules", "Reject (topic)"),
    "D11": DocumentClassSpec("D11", "Superseded or draft version", "Archived 2019 policy alongside 2024 policy", "Hold as version_of; not in current set unless population is historical"),
}


def all_codes() -> list[str]:
    return list(DOCUMENT_CLASS_REGISTRY.keys())
