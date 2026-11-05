"""Tests for the actual file-loading path in gold_loader.py -- coverage
analysis found this at 35%, meaning load_gold_file() itself (as opposed to
the legacy-mapping logic it calls) was essentially untested by pytest,
only exercised manually via the CLI.
"""
import json

import pytest

from polaris.evaluator.gold_loader import load_gold_file


def test_load_canonical_gold_file(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(json.dumps([
        {"text": "Employees must not accept gifts.", "true_label": "RUL"},
        {"text": "A gift means anything of value.", "label": "DEF"},  # alternate key name
    ]))
    rows = load_gold_file(path)
    assert len(rows) == 2
    assert rows[0].true_label == "RUL"
    assert rows[1].true_label == "DEF"
    assert all(not r.ambiguous for r in rows)


def test_load_gold_file_accepts_key_variants(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(json.dumps([
        {"paragraph": "text via alt key", "gold_label": "PUR"},
        {"segment_text": "another alt key", "class": "DEF"},
        {"content": "yet another", "true_label": "LAW"},
    ]))
    rows = load_gold_file(path)
    assert len(rows) == 3
    assert [r.true_label for r in rows] == ["PUR", "DEF", "LAW"]


def test_load_gold_file_missing_text_or_label_raises_with_useful_message(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(json.dumps([{"true_label": "RUL"}]))  # no text field at all
    with pytest.raises(ValueError, match="missing text or label"):
        load_gold_file(path)


def test_load_gold_file_not_a_list_raises(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text(json.dumps({"text": "oops, this is an object not a list"}))
    with pytest.raises(ValueError, match="JSON array"):
        load_gold_file(path)


def test_load_gold_file_missing_file_propagates_file_not_found_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_gold_file(tmp_path / "does_not_exist.json")


def test_load_gold_file_malformed_json_propagates(tmp_path):
    path = tmp_path / "gold.json"
    path.write_text("{not valid json,,,")
    with pytest.raises(json.JSONDecodeError):
        load_gold_file(path)


def test_load_legacy_taxonomy_direct_mapping(tmp_path):
    path = tmp_path / "legacy_gold.json"
    path.write_text(json.dumps([
        {"text": "some definition text", "true_label": "DEF"},
        {"text": "some scope text", "true_label": "SCOPE_EMPLOYEE"},
    ]))
    rows = load_gold_file(path, taxonomy="legacy")
    assert rows[0].true_label == "DEF"
    assert rows[1].true_label == "SCE"
    assert all(not r.ambiguous for r in rows)


def test_load_legacy_taxonomy_ambiguous_labels_tagged_not_guessed(tmp_path):
    path = tmp_path / "legacy_gold.json"
    path.write_text(json.dumps([
        {"text": "some rule text", "true_label": "RUL"},  # ambiguous in legacy scheme
    ]))
    rows = load_gold_file(path, taxonomy="legacy")
    assert len(rows) == 1
    assert rows[0].ambiguous is True
    assert rows[0].true_label is None  # never guessed


def test_load_legacy_taxonomy_drops_system_states_with_warning(tmp_path):
    path = tmp_path / "legacy_gold.json"
    path.write_text(json.dumps([
        {"text": "real content", "true_label": "DEF"},
        {"text": "a failed labelling attempt", "true_label": "FAIL"},
        {"text": "something else", "true_label": "OTHER"},
    ]))
    with pytest.warns(UserWarning, match="Dropped 2 row"):
        rows = load_gold_file(path, taxonomy="legacy")
    assert len(rows) == 1
    assert rows[0].true_label == "DEF"


def test_load_legacy_taxonomy_drops_unrecognised_label_via_value_error_path(tmp_path):
    """Distinct from the SYSTEM_STATES (OTHER/FAIL) drop path -- this hits
    map_legacy_label's raise ValueError branch for a label matching none
    of the known legacy categories at all."""
    path = tmp_path / "legacy_gold.json"
    path.write_text(json.dumps([
        {"text": "real content", "true_label": "DEF"},
        {"text": "garbage label", "true_label": "COMPLETELY_UNKNOWN_LEGACY_LABEL"},
    ]))
    with pytest.warns(UserWarning, match="Dropped 1 row"):
        rows = load_gold_file(path, taxonomy="legacy")
    assert len(rows) == 1
