"""Apostrophe variants in possessive and genitive suffix matching."""

from __future__ import annotations

import pytest
from septum_core import text_utils
from septum_core.text_utils import APOSTROPHES, strip_possessive_suffix


@pytest.mark.parametrize("apostrophe", sorted(APOSTROPHES))
def test_english_possessive_strips_every_apostrophe_variant(apostrophe: str) -> None:
    assert strip_possessive_suffix(f"John Smith{apostrophe}s", "en") == "John Smith"


@pytest.mark.parametrize("apostrophe", sorted(APOSTROPHES))
def test_turkish_suffix_strips_every_apostrophe_variant(apostrophe: str) -> None:
    assert strip_possessive_suffix(f"Kaya{apostrophe}nın", "tr") == "Kaya"


def test_returned_text_keeps_the_original_characters() -> None:
    assert strip_possessive_suffix("Smithʼs", "en") == "Smith"
    assert strip_possessive_suffix("Smith＇s", "en") == "Smith"
    assert strip_possessive_suffix("Ayşe Kaya’nın", "tr") == "Ayşe Kaya"


def test_name_without_suffix_is_unchanged() -> None:
    assert strip_possessive_suffix("Smith", "en") == "Smith"
    assert strip_possessive_suffix("Kaya’", "tr") == "Kaya’"


def test_common_suffix_table_has_no_duplicates() -> None:
    suffixes = text_utils._COMMON_POSSESSIVE_SUFFIXES
    assert len(set(suffixes)) == len(suffixes)


@pytest.mark.parametrize("apostrophe", ["‘", "´", "′"])
def test_added_apostrophe_variants_fold_like_ascii(apostrophe: str) -> None:
    assert apostrophe in APOSTROPHES
    assert strip_possessive_suffix(f"John Smith{apostrophe}s", "en") == "John Smith"
    assert strip_possessive_suffix(f"Kaya{apostrophe}nın", "tr") == "Kaya"


def test_backtick_is_not_an_apostrophe() -> None:
    assert "`" not in APOSTROPHES
    assert strip_possessive_suffix("John Smith`s", "en") == "John Smith`s"


def test_plural_possessive_strips_only_the_apostrophe() -> None:
    assert strip_possessive_suffix("James'", "en") == "James"
    assert strip_possessive_suffix("Lloyds’", "en") == "Lloyds"
    assert strip_possessive_suffix("Lloyd’s", "en") == "Lloyd"
    assert strip_possessive_suffix("Lloyds’", "en") != strip_possessive_suffix("Lloyd’s", "en")


def test_possessive_strip_returns_the_original_apostrophes() -> None:
    assert strip_possessive_suffix("O’Brien’s", "en") == "O’Brien"
    assert strip_possessive_suffix("O'Brien's", "en") == "O'Brien"
