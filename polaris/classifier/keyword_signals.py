"""Cheap keyword pre-filter, pass 1 of the classification cascade.

Job: decide, for near-zero cost, "is this segment obviously NOL (boilerplate/
non-substantive), or does it need the LLM to actually classify it into one
of the 17 substantive LETs." This is not the classifier -- it is a filter
that lets the pipeline skip an LLM call for the segments that clearly don't
need one (tables of contents, copyright footers, page furniture that
survived pdf_segmenter's boilerplate pass, one-word lines, etc.), which
matters at scale: a 40-page policy might have 200 segments, and calling
gpt-4o on every one of them when a third are structurally not policy
content is wasted latency and cost.

Design constraint from the conversation with Mada: this must not become a
second, competing taxonomy. It only ever returns two things: "confidently
NOL, skip the LLM" or "send to LLM." It never guesses at RUL vs THR vs DEF
itself -- that nuance needs the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MIN_SUBSTANTIVE_WORDS = 6

BOILERPLATE_PATTERNS = [
    re.compile(r"^\s*table of contents\s*$", re.IGNORECASE),
    re.compile(r"^\s*page \d+( of \d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*©.*all rights reserved\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*confidential\s*[-–—]?\s*internal use only\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+(\.\d+)*\s*$"),  # bare clause numbers with nothing else
]

# Presence of any of these words is a strong hint the segment is substantive
# policy content worth a full LLM classification, regardless of length.
SUBSTANTIVE_SIGNAL_WORDS = [
    "shall", "must", "required", "prohibited", "threshold", "approval",
    "register", "registration", "escalate", "exception", "waiver",
    "defined as", "means", "applies to", "scope", "employee", "department",
    "government official", "gift", "hospitality",
]


@dataclass
class PreFilterResult:
    skip_llm: bool
    reason: str


def prefilter(text: str) -> PreFilterResult:
    stripped = text.strip()

    for pattern in BOILERPLATE_PATTERNS:
        if pattern.match(stripped):
            return PreFilterResult(skip_llm=True, reason=f"matched boilerplate pattern: {pattern.pattern}")

    word_count = len(stripped.split())
    if word_count < MIN_SUBSTANTIVE_WORDS:
        has_signal = any(w in stripped.lower() for w in SUBSTANTIVE_SIGNAL_WORDS)
        if not has_signal:
            return PreFilterResult(
                skip_llm=True,
                reason=f"only {word_count} words and no substantive signal term -- likely a heading/fragment",
            )

    return PreFilterResult(skip_llm=False, reason="passed pre-filter, routed to LLM classification")

# Failure modes: none beyond normal Python errors. Pure regex matching on
# a string -- cannot raise anything a caller would need to catch, and
# wrapping this in try/except would only hide a genuine bug in a caller
# passing a non-string.
