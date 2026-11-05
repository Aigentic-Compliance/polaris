import pytest

from polaris.evaluator.legacy_label_mapping import (
    AmbiguousLegacyLabelError, map_legacy_label,
)


def test_direct_mapping_resolves():
    label, ambiguous = map_legacy_label("DEF")
    assert label == "DEF"
    assert ambiguous is False


def test_ambiguous_mapping_raises_by_default():
    with pytest.raises(AmbiguousLegacyLabelError):
        map_legacy_label("RUL")


def test_ambiguous_mapping_tagged_not_guessed_when_allowed():
    label, ambiguous = map_legacy_label("RUL", allow_ambiguous=True)
    assert label is None  # never guesses which of the 8 candidates is correct
    assert ambiguous is True


def test_no_canonical_equivalent_returns_none_ambiguous_true():
    label, ambiguous = map_legacy_label("SCOPE_INTERNAL_EXTERNAL", allow_ambiguous=True)
    assert label is None
    assert ambiguous is True


def test_unknown_label_raises_value_error():
    with pytest.raises(ValueError, match="Unknown legacy label"):
        map_legacy_label("SOME_MADE_UP_LABEL_THAT_DOES_NOT_EXIST")
