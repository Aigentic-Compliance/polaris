"""Load a gold-labelled evaluation file into a uniform internal shape.

Accepted input shape (JSON, list of objects):
    [{"text": "...", "true_label": "RUL", "segment_id": "optional"}, ...]

If true_label values are legacy 9-label codes, pass taxonomy="legacy" and
this loader will run them through legacy_label_mapping.py -- rows with an
ambiguous mapping are kept but tagged `ambiguous=True` and excluded from
the strict-accuracy metrics computed in metrics.py (they still appear in
the report as a separate "unscored -- ambiguous mapping" count so nothing
silently disappears).

This is intentionally forgiving about input shape (a few common key-name
variants) because we do not yet know the exact shape of the file Mada will
send -- better to accept close variants explicitly than to fail on a
trivial key-naming mismatch, or worse, silently coerce something incorrect.

Failure modes: a missing file or unreadable JSON is NOT caught here --
FileNotFoundError / json.JSONDecodeError propagate as-is. This is
deliberate: those are configuration errors ("you pointed this at the wrong
path" / "this file isn't valid JSON"), and wrapping them would only hide
which file and what line, since Path.read_text() and json.loads() already
say that clearly. A row missing both text and label fields raises
ValueError naming which keys WERE present, so a shape mismatch is
diagnosable from the error message alone rather than requiring a debugger
session.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from polaris.common.logging_config import get_logger
from polaris.evaluator.legacy_label_mapping import AmbiguousLegacyLabelError, map_legacy_label

logger = get_logger(__name__)

_TEXT_KEYS = ["text", "paragraph", "segment_text", "content"]
_LABEL_KEYS = ["true_label", "label", "gold_label", "class"]


@dataclass
class GoldRow:
    text: str
    true_label: str | None  # None only when ambiguous/unmappable
    ambiguous: bool
    source_row: dict


def _first_present(d: dict, keys: list[str]) -> str | None:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def load_gold_file(path: str | Path, taxonomy: str = "canonical") -> list[GoldRow]:
    """taxonomy: "canonical" (already 18-LET) or "legacy" (BUAS 9-label)."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        raise ValueError("Gold file must be a JSON array of {text, true_label} objects.")

    rows: list[GoldRow] = []
    dropped_system_state = 0

    for raw in data:
        text = _first_present(raw, _TEXT_KEYS)
        label = _first_present(raw, _LABEL_KEYS)
        if text is None or label is None:
            raise ValueError(f"Row missing text or label field. Keys present: {list(raw.keys())}")

        if taxonomy == "canonical":
            rows.append(GoldRow(text=text, true_label=label, ambiguous=False, source_row=raw))
            continue

        try:
            canonical, ambiguous = map_legacy_label(label, allow_ambiguous=True)
        except ValueError:
            dropped_system_state += 1
            continue
        if canonical is None and not ambiguous:
            dropped_system_state += 1
            continue
        rows.append(GoldRow(text=text, true_label=canonical, ambiguous=ambiguous, source_row=raw))

    if dropped_system_state:
        import warnings

        msg = (
            f"Dropped {dropped_system_state} row(s) with system-state or unknown legacy labels "
            f"(OTHER/FAIL/unrecognised) -- these are not content labels."
        )
        logger.warning(msg)
        warnings.warn(msg, stacklevel=2)
    return rows
