"""Render ComparisonFinding lists into a report, ranked by materiality and
confidence -- not by frequency, per v5.0 section 12.3's explicit
instruction. All output is marked review_state="draft"; nothing here is
ever auto-approved or auto-published (gate G5 is a human decision, not a
function in this codebase).
"""

from __future__ import annotations

from polaris.common.models import ComparisonFinding


def rank_findings(findings: list[ComparisonFinding]) -> list[ComparisonFinding]:
    return sorted(findings, key=lambda f: (f.materiality * f.confidence), reverse=True)


def render_text_report(findings: list[ComparisonFinding]) -> str:
    ranked = rank_findings(findings)
    lines = [f"POLARIS Recommender -- draft comparison report ({len(ranked)} findings, all review_state=draft)", ""]
    for f in ranked:
        lines.append(f"[{f.comparison_state}] materiality={f.materiality:.2f} confidence={f.confidence:.2f}")
        lines.append(f"  FACT: {f.fact}")
        if f.recommendation:
            lines.append(f"  DRAFT RECOMMENDATION (human decision required): {f.recommendation}")
        if f.evidence_object_ids:
            lines.append(f"  Evidence: {', '.join(f.evidence_object_ids)}")
        lines.append("")
    return "\n".join(lines)


def to_json_records(findings: list[ComparisonFinding]) -> list[dict]:
    return [f.model_dump() for f in rank_findings(findings)]

# Failure modes: render_text_report() and to_json_records() are pure
# formatting over already-valid ComparisonFinding objects -- nothing here
# can fail except a programmer error, which should raise, not be caught.
