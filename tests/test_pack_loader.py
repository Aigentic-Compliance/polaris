"""pack_loader.py had 0% pytest coverage before this audit -- exercised
only via scripts/run_recommender_demo.py against the synthetic fixtures,
never asserted on directly, including its error paths.
"""
import json

import pytest

from polaris.common.exceptions import GoldDataError
from polaris.recommender.pack_loader import load_objects

VALID_OBJECT = {
    "object_id": "THR-GFT-0001", "source_id": "11111111-1111-1111-1111-111111111111",
    "segment_id": "22222222-2222-2222-2222-222222222222", "source_ref": "clause 1",
    "label": "THR", "concept_id": "RRL-1", "concept_state": "mapped",
    "parameters": {"threshold_value": 50}, "pack_origin": "benchmark",
}


def test_load_objects_from_plain_list(tmp_path):
    path = tmp_path / "objects.json"
    path.write_text(json.dumps([VALID_OBJECT]))
    objects = load_objects(path)
    assert len(objects) == 1
    assert objects[0].object_id == "THR-GFT-0001"


def test_load_objects_from_wrapped_dict(tmp_path):
    """Tolerates a pack-shaped {"objects": [...]} wrapper."""
    path = tmp_path / "objects.json"
    path.write_text(json.dumps({"objects": [VALID_OBJECT], "pack_version": "1.4.0"}))
    objects = load_objects(path)
    assert len(objects) == 1


def test_load_objects_invalid_object_raises_gold_data_error_naming_index(tmp_path):
    bad_object = dict(VALID_OBJECT)
    bad_object["label"] = "NOT_A_REAL_LET_CODE"  # fails the enum constraint
    path = tmp_path / "objects.json"
    path.write_text(json.dumps([VALID_OBJECT, bad_object]))

    with pytest.raises(GoldDataError, match="index 1"):
        load_objects(path)


def test_load_objects_missing_file_propagates(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_objects(tmp_path / "nonexistent.json")


def test_load_objects_empty_list(tmp_path):
    path = tmp_path / "objects.json"
    path.write_text(json.dumps([]))
    assert load_objects(path) == []
