"""Load a list of PolicyObject records (either side: benchmark or customer)
from a JSON file shaped like a pack's objects.json.

This loader does not know or care whether the file came from a real signed
Policron pack, a benchmark release, or a test fixture -- that is exactly
the point of the comparator using one canonical PolicyObject shape for both
sides (per v5.0 section 12: "the benchmark factory and customer pipeline
share the same canonical representation").

Important: this repo does not contain, and must never contain, real
Aigentic pack content (PACK-GFT or any customer pack). See
fixtures/pack_synthetic/README.md.

Failure modes: a missing file or invalid JSON propagates unchanged
(FileNotFoundError / json.JSONDecodeError already say exactly what's
wrong). A single malformed object inside an otherwise-valid file is the
case worth handling deliberately: pydantic's ValidationError on a bare
list comprehension only tells you *what* failed, not *which of 200
objects* -- so this loader validates one at a time and raises
GoldDataError naming the file, the index, and (if present) the object_id,
so a bad pack export is diagnosable without a debugger.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from polaris.common.exceptions import GoldDataError
from polaris.common.logging_config import get_logger
from polaris.common.models import PolicyObject

logger = get_logger(__name__)


def load_objects(path: str | Path) -> list[PolicyObject]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "objects" in data:
        data = data["objects"]  # tolerate a pack-shaped {"objects": [...]} wrapper

    objects: list[PolicyObject] = []
    for index, obj in enumerate(data):
        try:
            objects.append(PolicyObject.model_validate(obj))
        except ValidationError as exc:
            object_id = obj.get("object_id", "<no object_id field>") if isinstance(obj, dict) else "<not a dict>"
            logger.error("invalid PolicyObject at index %d (object_id=%s) in %s: %s", index, object_id, path, exc)
            raise GoldDataError(
                f"{path}: object at index {index} (object_id={object_id!r}) failed contract "
                f"validation: {exc}"
            ) from exc
    return objects
